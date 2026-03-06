"""
KLAUD-NINJA — Moderation Cog
Listens to every message in licensed guilds, runs AI classification,
and applies the appropriate punishment based on the configured intensity.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

import discord
from discord.ext import commands

from ai.groq_client import GroqClient
from ai.prompts     import MODERATION_SYSTEM, moderation_user_prompt
from database       import queries
from utils.permissions import can_moderate_target

log = logging.getLogger("klaud.moderation")

# ── Action ordering (used for tier-capping) ───────────────────────────────────
_ACTION_ORDER  = ["none", "warn", "delete", "timeout", "kick", "ban"]

# Maximum action allowed per intensity level
_MAX_ACTION: dict[str, str] = {
    "LOW":     "warn",
    "MEDIUM":  "timeout",
    "HIGH":    "kick",
    "EXTREME": "ban",
}


class ModerationCog(commands.Cog, name="Moderation"):
    """
    Real-time AI message moderation.

    • Runs on every message (bots excluded).
    • Skips messages from admins/moderators.
    • Calls Groq AI for violation classification.
    • Applies punishment and logs to Supabase.
    """

    def __init__(self, bot: commands.Bot, groq: GroqClient) -> None:
        self.bot  = bot
        self.groq = groq

    # ── Message listener ──────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Skip bots, DMs, very short messages
        if message.author.bot or not message.guild:
            return
        if len(message.content) < 3:
            return

        # Skip admins / moderators
        member = message.guild.get_member(message.author.id)
        if member and (
            member.guild_permissions.administrator
            or member.guild_permissions.manage_messages
        ):
            return

        # Fetch guild settings
        settings = await queries.get_or_create_guild_settings(message.guild.id)
        if not settings.get("ai_enabled", True):
            return

        intensity = str(settings.get("moderation_level", "MEDIUM")).upper()

        # Call AI
        result = await self.groq.complete_json(
            system=MODERATION_SYSTEM,
            user=moderation_user_prompt(message.content, intensity),
            max_tokens=256,
            operation="moderation",
        )

        if not result or not isinstance(result, dict):
            return
        if not result.get("violation"):
            return

        action  = str(result.get("action", "none")).lower()
        reason  = str(result.get("reason",  "AI moderation"))

        # Cap action at intensity maximum
        max_act = _MAX_ACTION.get(intensity, "ban")
        if _ACTION_ORDER.index(action) > _ACTION_ORDER.index(max_act):
            action = max_act

        if action == "none":
            return

        # Permission check
        bot_member = message.guild.me
        can, why   = can_moderate_target(bot_member, member)
        if not can:
            log.debug(f"Skipping moderation on {member}: {why}")
            return

        # Execute punishment
        await self._apply(message, member, action, reason)

        # Log to Supabase
        await queries.log_infraction(
            guild_id=message.guild.id,
            user_id=message.author.id,
            reason=reason,
            action=action,
        )

        # Log channel embed
        log_channel_id = settings.get("log_channel")
        if log_channel_id:
            log_ch = message.guild.get_channel(int(log_channel_id))
            if log_ch:
                await self._send_log_embed(log_ch, message, member, action, reason, result)

    # ── Punishment executor ────────────────────────────────────────────────────

    async def _apply(
        self,
        message: discord.Message,
        member:  discord.Member,
        action:  str,
        reason:  str,
    ) -> None:
        audit = f"KLAUD AI: {reason}"

        try:
            if action == "warn":
                await message.channel.send(
                    f"⚠️ {member.mention} — **Warning:** {reason}",
                    delete_after=15,
                )

            elif action == "delete":
                await message.delete()
                await message.channel.send(
                    f"🗑️ {member.mention}'s message was removed. **Reason:** {reason}",
                    delete_after=10,
                )

            elif action == "timeout":
                await message.delete()
                await member.timeout(timedelta(minutes=10), reason=audit)
                await message.channel.send(
                    f"🔇 {member.mention} timed out for 10 minutes. **Reason:** {reason}",
                    delete_after=15,
                )

            elif action == "kick":
                await message.delete()
                try:
                    await member.send(f"You were kicked from **{message.guild.name}**. Reason: {reason}")
                except discord.HTTPException:
                    pass
                await member.kick(reason=audit)

            elif action == "ban":
                await message.delete()
                try:
                    await member.send(f"You were banned from **{message.guild.name}**. Reason: {reason}")
                except discord.HTTPException:
                    pass
                await member.ban(reason=audit, delete_message_days=1)

        except discord.Forbidden:
            log.warning(f"Missing permission for {action} on {member}")
        except discord.HTTPException as exc:
            log.error(f"HTTP error during {action}: {exc}")

    # ── Log embed ─────────────────────────────────────────────────────────────

    async def _send_log_embed(
        self,
        log_channel: discord.TextChannel,
        message:     discord.Message,
        member:      discord.Member,
        action:      str,
        reason:      str,
        ai_result:   dict,
    ) -> None:
        colors = {
            "warn":    discord.Color.yellow(),
            "delete":  discord.Color.orange(),
            "timeout": discord.Color.red(),
            "kick":    discord.Color.dark_red(),
            "ban":     discord.Color.dark_red(),
        }
        embed = discord.Embed(
            title=f"🛡️ Moderation Action — {action.upper()}",
            color=colors.get(action, discord.Color.greyple()),
        )
        embed.add_field(name="User",       value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Action",     value=action.title(),                      inline=True)
        embed.add_field(name="Severity",   value=ai_result.get("severity", "?"),      inline=True)
        embed.add_field(name="Reason",     value=reason,                              inline=False)
        embed.add_field(
            name="Categories",
            value=", ".join(ai_result.get("categories", [])) or "—",
            inline=False,
        )
        if message.content:
            truncated = message.content[:400] + ("…" if len(message.content) > 400 else "")
            embed.add_field(name="Original Message", value=f"```{truncated}```", inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Guild: {message.guild.name} • Klaud-Ninja AI")
        embed.timestamp = discord.utils.utcnow()
        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    groq: GroqClient = bot.groq  # type: ignore[attr-defined]
    await bot.add_cog(ModerationCog(bot, groq))
