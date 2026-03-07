"""
KLAUD-NINJA — Moderation Cog
AI-powered message moderation using Groq.
Intensity: LOW / MEDIUM / HIGH / EXTREME
Punishments: warn → delete → timeout → kick → ban
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.bot import KlaudBot
from services.groq_service import ModerationAction, ModerationDecision

logger = logging.getLogger("klaud.moderation")

_MIN_CONFIDENCE: dict[str, float] = {
    "LOW":     0.85,
    "MEDIUM":  0.70,
    "HIGH":    0.55,
    "EXTREME": 0.40,
}

_TIER_MAX_ACTION: dict[str, ModerationAction] = {
    "BASIC":      ModerationAction.DELETE,
    "PRO":        ModerationAction.KICK,
    "ENTERPRISE": ModerationAction.BAN,
}

_ACTION_ORDER = [
    ModerationAction.NONE,
    ModerationAction.WARN,
    ModerationAction.DELETE,
    ModerationAction.TIMEOUT,
    ModerationAction.KICK,
    ModerationAction.BAN,
]


class ModerationCog(commands.Cog, name="Moderation"):
    def __init__(self, bot: KlaudBot) -> None:
        self.bot = bot
        self._intensity_cache: dict[int, str] = {}

    # ─── Slash commands ───────────────────────────────────────────────────────

    mod_group = app_commands.Group(name="mod", description="Moderation settings and tools")

    @mod_group.command(name="intensity", description="Set the AI moderation intensity for this server")
    @app_commands.describe(level="LOW | MEDIUM | HIGH | EXTREME")
    @app_commands.choices(level=[
        app_commands.Choice(name="LOW — Only extreme violations",   value="LOW"),
        app_commands.Choice(name="MEDIUM — Balanced (default)",     value="MEDIUM"),
        app_commands.Choice(name="HIGH — Strict enforcement",       value="HIGH"),
        app_commands.Choice(name="EXTREME — Zero tolerance",        value="EXTREME"),
    ])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_intensity(self, interaction: discord.Interaction, level: str) -> None:
        if not await self.bot.assert_licensed(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        level = level.upper()
        await self._save_intensity(guild_id, level)
        self._intensity_cache[guild_id] = level
        color_map = {"LOW": discord.Color.green(), "MEDIUM": discord.Color.blue(),
                     "HIGH": discord.Color.orange(), "EXTREME": discord.Color.red()}
        descriptions = {
            "LOW":     "Only acts on extreme violations like threats, hate speech, and scams.",
            "MEDIUM":  "Balanced enforcement. Handles clear toxicity, harassment, and spam.",
            "HIGH":    "Strict enforcement. Catches profanity, caps abuse, and invite links.",
            "EXTREME": "Zero tolerance. Acts on anything suspicious or borderline.",
        }
        embed = discord.Embed(
            title="⚙️ Moderation Intensity Updated",
            description=f"AI moderation level set to **{level}**\n\n{descriptions.get(level,'')}",
            color=color_map.get(level, discord.Color.blue()),
        )
        await interaction.followup.send(embed=embed, ephemeral=False)
        logger.info(f"Intensity set | guild={guild_id} | level={level}")

    @mod_group.command(name="status", description="Show current moderation configuration")
    async def mod_status(self, interaction: discord.Interaction) -> None:
        if not await self.bot.assert_licensed(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        guild_id  = interaction.guild.id
        intensity = await self._get_intensity(guild_id)
        tier      = await self.bot.license_manager.get_tier(guild_id)
        log_ch    = await self.bot.get_mod_log_channel(interaction.guild)
        max_action = _TIER_MAX_ACTION.get(tier, ModerationAction.DELETE)
        embed = discord.Embed(title="🛡️ Moderation Status", color=discord.Color.blurple())
        embed.add_field(name="License Tier",   value=tier,                                        inline=True)
        embed.add_field(name="AI Intensity",   value=intensity,                                   inline=True)
        embed.add_field(name="Mod Log",        value=log_ch.mention if log_ch else "Not set",     inline=True)
        embed.add_field(name="AI Available",   value="✅ Yes" if self.bot.groq.available else "⚠️ Fallback", inline=True)
        embed.add_field(name="Max Punishment", value=max_action.value.upper(),                    inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @mod_group.command(name="warn", description="Manually warn a user")
    @app_commands.describe(user="User to warn", reason="Reason for warning")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def manual_warn(self, interaction: discord.Interaction,
                           user: discord.Member, reason: str = "No reason provided") -> None:
        if not await self.bot.assert_licensed(interaction):
            return
        await interaction.response.defer(ephemeral=False)
        await self._execute_warn(user, interaction.guild, reason,
                                  channel=interaction.channel,
                                  moderator_id=interaction.user.id)
        await interaction.followup.send(f"⚠️ {user.mention} has been warned. Reason: {reason}")

    @mod_group.command(name="history", description="View moderation history for a user")
    @app_commands.describe(user="User to look up")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def user_history(self, interaction: discord.Interaction, user: discord.Member) -> None:
        if not await self.bot.assert_licensed(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        if self.bot.db.is_postgres():
            rows = await self.bot.db.fetch(
                "SELECT action, reason, created_at, ai_confidence FROM mod_actions "
                "WHERE guild_id=$1 AND user_id=$2 ORDER BY created_at DESC LIMIT 10",
                interaction.guild.id, user.id,
            )
        elif self.bot.db.is_sqlite():
            rows = await self.bot.db.fetch(
                None, interaction.guild.id, user.id,
                sqlite_query=(
                    "SELECT action, reason, created_at, ai_confidence FROM mod_actions "
                    "WHERE guild_id=? AND user_id=? ORDER BY created_at DESC LIMIT 10"
                ),
            )
        else:
            rows = []
        embed = discord.Embed(title=f"📋 Mod History: {user.display_name}", color=discord.Color.orange())
        if not rows:
            embed.description = "No moderation history found."
        else:
            lines = []
            for r in rows:
                ts     = str(r.get("created_at",""))[:16]
                action = str(r.get("action","?")).upper()
                reason = r.get("reason","")[:60]
                conf   = r.get("ai_confidence")
                lines.append(f"`{ts}` **{action}**{f' ({conf:.0%})' if conf else ''} — {reason}")
            embed.description = "\n".join(lines)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── Message listener ─────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return
        if message.author.id == self.bot.settings.BOT_OWNER_ID:
            return

        licensed = await self.bot.license_manager.is_licensed(message.guild.id)
        if not licensed:
            return

        member = message.author
        if isinstance(member, discord.Member):
            if member.guild_permissions.administrator or member.guild_permissions.manage_messages:
                return

        intensity = await self._get_intensity(message.guild.id)

        # Fast spam check
        msg_count = self.bot.track_spam(message.guild.id, message.author.id)
        if msg_count >= self.bot.settings.SPAM_THRESHOLD_MESSAGES:
            decision = ModerationDecision(
                action=ModerationAction.TIMEOUT,
                confidence=0.95,
                categories=["spam"],
                reason=f"Spam: {msg_count} messages sent too fast",
                timeout_duration=self.bot.settings.DEFAULT_TIMEOUT_DURATION,
                delete_message=True,
                ai_generated=False,
            )
            await self._apply_decision(message, decision, intensity)
            return

        if len(message.content) < 3 and intensity in ("LOW", "MEDIUM"):
            return

        # AI analysis
        try:
            decision = await asyncio.wait_for(
                self.bot.groq.analyze_message(
                    content=message.content,
                    intensity=intensity,
                    author_info=f"{message.author} (id={message.author.id})",
                    channel_info=f"#{message.channel.name}",
                ),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Moderation timeout | guild={message.guild.id}")
            return
        except Exception as e:
            logger.error(f"Moderation analysis error: {e}")
            return

        if decision.action != ModerationAction.NONE:
            await self._apply_decision(message, decision, intensity)

    # ─── Decision executor ────────────────────────────────────────────────────

    async def _apply_decision(
        self,
        message: discord.Message,
        decision: ModerationDecision,
        intensity: str,
    ) -> None:
        guild  = message.guild
        member = message.author
        action = decision.action

        # Confidence gate
        min_conf = _MIN_CONFIDENCE.get(intensity, 0.70)
        if decision.confidence < min_conf:
            return

        # Tier cap
        tier        = await self.bot.license_manager.get_tier(guild.id)
        max_allowed = _TIER_MAX_ACTION.get(tier, ModerationAction.DELETE)
        if _ACTION_ORDER.index(action) > _ACTION_ORDER.index(max_allowed):
            action = max_allowed

        if action == ModerationAction.NONE:
            return

        # Bot permission check — avoid crashing on missing perms
        bot_member = guild.me
        if not bot_member:
            return

        # Delete message if needed
        if decision.delete_message or action not in (ModerationAction.WARN,):
            try:
                await message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass

        # Execute punishment
        executed_action = action
        channel = message.channel

        try:
            if action == ModerationAction.WARN:
                warn_count = await self._execute_warn(member, guild, decision.reason, channel=channel)
                if warn_count >= 3 and tier in ("PRO", "ENTERPRISE"):
                    executed_action = ModerationAction.TIMEOUT
                    await self._execute_timeout(member, guild, decision.reason,
                                                 decision.timeout_duration, channel=channel)

            elif action == ModerationAction.DELETE:
                await self._execute_warn(member, guild, decision.reason, channel=channel)

            elif action == ModerationAction.TIMEOUT:
                await self._execute_timeout(member, guild, decision.reason,
                                             decision.timeout_duration, channel=channel)

            elif action == ModerationAction.KICK:
                await self._execute_kick(member, guild, decision.reason, channel=channel)

            elif action == ModerationAction.BAN:
                await self._execute_ban(member, guild, decision.reason, channel=channel)

        except discord.Forbidden:
            logger.warning(f"Missing perms for {action} on {member} in {guild.name}")
            try:
                await channel.send(
                    f"⚠️ I tried to **{action.value}** {member.mention} but I'm missing permissions. "
                    f"Please make sure my role is **above** theirs.",
                    delete_after=10,
                )
            except Exception:
                pass
            return
        except Exception as e:
            logger.error(f"Failed to execute {action} on {member}: {e}")
            return

        # Mod log
        await self._send_mod_log(message, decision, executed_action, tier)

        # DB log
        await self.bot.log_mod_action(
            guild_id=guild.id,
            user_id=member.id,
            moderator_id=0,
            action=executed_action.value,
            reason=decision.reason,
            message_content=message.content[:500] if message.content else None,
            channel_id=message.channel.id,
            duration_secs=decision.timeout_duration if action == ModerationAction.TIMEOUT else None,
            ai_confidence=decision.confidence,
            ai_categories=decision.categories,
        )

        logger.info(
            f"Moderated | guild={guild.id} | user={member.id} | "
            f"action={executed_action.value} | confidence={decision.confidence:.2f} | "
            f"categories={decision.categories} | ai={decision.ai_generated}"
        )

    # ─── Punishment executors ─────────────────────────────────────────────────

    async def _execute_warn(
        self,
        member: discord.Member,
        guild:  discord.Guild,
        reason: str,
        channel: Optional[discord.TextChannel] = None,
        moderator_id: int = 0,
    ) -> int:
        warn_count = await self.bot.increment_warn_count(guild.id, member.id)

        # Public notification in the channel
        if channel:
            try:
                await channel.send(
                    f"⚠️ {member.mention} has been **warned** by AI moderation.\n"
                    f"**Reason:** {reason}\n"
                    f"*Warning #{warn_count}*",
                    delete_after=15,
                )
            except (discord.Forbidden, discord.NotFound):
                pass

        # DM the user
        try:
            embed = discord.Embed(
                title="⚠️ Warning Received",
                description=f"You received a warning in **{guild.name}**.",
                color=discord.Color.yellow(),
            )
            embed.add_field(name="Reason",    value=reason,         inline=False)
            embed.add_field(name="Warning #", value=str(warn_count), inline=True)
            embed.set_footer(text="Continued violations may result in stronger action.")
            await member.send(embed=embed)
        except discord.Forbidden:
            pass

        return warn_count

    async def _execute_timeout(
        self,
        member:       discord.Member,
        guild:        discord.Guild,
        reason:       str,
        duration_secs: int,
        channel:      Optional[discord.TextChannel] = None,
    ) -> None:
        until = discord.utils.utcnow() + timedelta(seconds=duration_secs)
        await member.timeout(until, reason=f"[KLAUD AI] {reason}"[:512])
        minutes = duration_secs // 60

        # Public notification
        if channel:
            try:
                await channel.send(
                    f"⏱️ {member.mention} has been **timed out** for **{minutes} minute(s)**.\n"
                    f"**Reason:** {reason}",
                    delete_after=20,
                )
            except (discord.Forbidden, discord.NotFound):
                pass

        # DM
        try:
            embed = discord.Embed(
                title="⏱️ You Have Been Timed Out",
                description=f"You have been timed out in **{guild.name}**.",
                color=discord.Color.orange(),
            )
            embed.add_field(name="Reason",   value=reason,              inline=False)
            embed.add_field(name="Duration", value=f"{minutes} minutes", inline=True)
            await member.send(embed=embed)
        except discord.Forbidden:
            pass

    async def _execute_kick(
        self,
        member:  discord.Member,
        guild:   discord.Guild,
        reason:  str,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        # Public notification before kick
        if channel:
            try:
                await channel.send(
                    f"🦶 {member.mention} has been **kicked**.\n**Reason:** {reason}",
                    delete_after=20,
                )
            except (discord.Forbidden, discord.NotFound):
                pass

        # DM
        try:
            embed = discord.Embed(
                title="🦶 You Have Been Kicked",
                description=f"You have been kicked from **{guild.name}**.",
                color=discord.Color.red(),
            )
            embed.add_field(name="Reason", value=reason, inline=False)
            await member.send(embed=embed)
        except discord.Forbidden:
            pass

        await guild.kick(member, reason=f"[KLAUD AI] {reason}"[:512])

    async def _execute_ban(
        self,
        member:  discord.Member,
        guild:   discord.Guild,
        reason:  str,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        # Public notification before ban
        if channel:
            try:
                await channel.send(
                    f"🔨 {member.mention} has been **banned**.\n**Reason:** {reason}",
                    delete_after=20,
                )
            except (discord.Forbidden, discord.NotFound):
                pass

        # DM
        try:
            embed = discord.Embed(
                title="🔨 You Have Been Banned",
                description=f"You have been banned from **{guild.name}**.",
                color=discord.Color.dark_red(),
            )
            embed.add_field(name="Reason", value=reason, inline=False)
            await member.send(embed=embed)
        except discord.Forbidden:
            pass

        await guild.ban(member, reason=f"[KLAUD AI] {reason}"[:512], delete_message_days=1)

    # ─── Mod log embed ─────────────────────────────────────────────────────────

    async def _send_mod_log(
        self,
        message:  discord.Message,
        decision: ModerationDecision,
        action:   ModerationAction,
        tier:     str,
    ) -> None:
        log_channel = await self.bot.get_mod_log_channel(message.guild)
        if not log_channel:
            return

        color_map = {
            ModerationAction.WARN:    discord.Color.yellow(),
            ModerationAction.DELETE:  discord.Color.orange(),
            ModerationAction.TIMEOUT: discord.Color.orange(),
            ModerationAction.KICK:    discord.Color.red(),
            ModerationAction.BAN:     discord.Color.dark_red(),
        }
        emoji_map = {
            ModerationAction.WARN:    "⚠️",
            ModerationAction.DELETE:  "🗑️",
            ModerationAction.TIMEOUT: "⏱️",
            ModerationAction.KICK:    "🦶",
            ModerationAction.BAN:     "🔨",
        }

        embed = discord.Embed(
            title=f"{emoji_map.get(action,'🛡️')} Auto-Mod: {action.value.upper()}",
            color=color_map.get(action, discord.Color.blurple()),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=message.author.display_avatar.url)
        embed.add_field(name="User",       value=f"{message.author.mention} (`{message.author.id}`)", inline=True)
        embed.add_field(name="Channel",    value=message.channel.mention,                            inline=True)
        embed.add_field(name="Action",     value=action.value.upper(),                               inline=True)
        embed.add_field(name="Reason",     value=decision.reason[:200],                              inline=False)
        embed.add_field(name="Categories", value=", ".join(decision.categories) or "—",              inline=True)
        embed.add_field(name="Confidence", value=f"{decision.confidence:.0%}",                       inline=True)
        embed.add_field(name="Source",     value="Groq AI" if decision.ai_generated else "Fallback", inline=True)

        if message.content:
            preview = message.content[:300] + ("..." if len(message.content) > 300 else "")
            embed.add_field(name="Message", value=f"```{preview}```", inline=False)

        embed.set_footer(text=f"KLAUD-NINJA • Tier: {tier}")
        try:
            await log_channel.send(embed=embed)
        except (discord.Forbidden, discord.NotFound):
            pass
        except Exception as e:
            logger.error(f"Mod log error: {e}")

    # ─── Intensity helpers ────────────────────────────────────────────────────

    async def _get_intensity(self, guild_id: int) -> str:
        if guild_id in self._intensity_cache:
            return self._intensity_cache[guild_id]
        try:
            if self.bot.db.is_postgres():
                row = await self.bot.db.fetchrow(
                    "SELECT mod_intensity FROM guild_settings WHERE guild_id=$1", guild_id)
            elif self.bot.db.is_sqlite():
                row = await self.bot.db.fetchrow(
                    None, guild_id,
                    sqlite_query="SELECT mod_intensity FROM guild_settings WHERE guild_id=?")
            else:
                row = None
            intensity = row["mod_intensity"] if row else "MEDIUM"
        except Exception:
            intensity = "MEDIUM"
        self._intensity_cache[guild_id] = intensity
        return intensity

    async def _save_intensity(self, guild_id: int, intensity: str) -> None:
        try:
            if self.bot.db.is_postgres():
                await self.bot.db.execute(
                    "INSERT INTO guild_settings (guild_id, mod_intensity) VALUES ($1,$2) "
                    "ON CONFLICT (guild_id) DO UPDATE SET mod_intensity=$2, updated_at=NOW()",
                    guild_id, intensity,
                )
            elif self.bot.db.is_sqlite():
                existing = await self.bot.db.fetchrow(
                    None, guild_id,
                    sqlite_query="SELECT guild_id FROM guild_settings WHERE guild_id=?")
                if existing:
                    await self.bot.db.execute(
                        None, intensity, guild_id,
                        sqlite_query="UPDATE guild_settings SET mod_intensity=? WHERE guild_id=?")
                else:
                    await self.bot.db.execute(
                        None, guild_id, intensity,
                        sqlite_query="INSERT INTO guild_settings (guild_id,mod_intensity) VALUES (?,?)")
        except Exception as e:
            logger.error(f"Failed to save intensity for guild {guild_id}: {e}")


async def setup(bot: KlaudBot) -> None:
    await bot.add_cog(ModerationCog(bot))
