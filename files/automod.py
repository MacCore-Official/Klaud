"""
moderation/automod.py — AI-powered auto-moderation Cog.

Listens to every message. For each message it:
  1. Skips bots and DMs.
  2. Checks if the guild has a valid license.
  3. Pulls custom rules from PromptManager.
  4. Sends content to Groq moderation_check().
  5. Executes the recommended action (warn / delete / timeout).
  6. Escalates punishments based on warning count.
  7. Logs all actions to Supabase.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import discord
from discord.ext import commands

from ai.core import moderation_check
from ai.prompt_manager import prompt_manager
from database import db_client
from moderation.warnings import issue_warning
import config

log = logging.getLogger(__name__)


class AutoMod(commands.Cog, name="AutoMod"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── License guard ──────────────────────────────────────────────────────────

    async def _guild_licensed(self, guild_id: int) -> bool:
        lic = await db_client.get_license(guild_id)
        return lic is not None and lic.get("active", False)

    # ── Core listener ──────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Ignore bots and DMs
        if message.author.bot or not message.guild:
            return

        guild_id = message.guild.id

        # Skip if not licensed
        if not await self._guild_licensed(guild_id):
            return

        # Skip admins / server owner
        member = message.author
        if (
            isinstance(member, discord.Member)
            and (member.guild_permissions.administrator or member.id == message.guild.owner_id)
        ):
            return

        # Fetch combined custom rules for this guild
        rules = await prompt_manager.get_combined_rules(guild_id)

        # Run AI moderation check
        result = await moderation_check(message.content, custom_rules=rules)

        if not result.get("violation"):
            return

        action = result.get("action", "none")
        reason = result.get("reason", "AI automod violation")
        severity = result.get("severity", "low")

        log.info(
            "AutoMod [%s] guild=%s user=%s action=%s severity=%s",
            result.get("categories"),
            guild_id,
            member.id,
            action,
            severity,
        )

        # Always delete on medium/high severity
        if severity in ("medium", "high") or action == "delete":
            try:
                await message.delete()
            except discord.Forbidden:
                log.warning("Cannot delete message in guild %s — missing permissions", guild_id)

        # Execute actions
        if action in ("warn", "delete", "timeout", "ban"):
            warn_count = await issue_warning(
                guild=message.guild,
                user=member,
                moderator=self.bot.user,
                reason=f"[AutoMod] {reason}",
            )

            try:
                await member.send(
                    f"⚠️ **Warning** in **{message.guild.name}**\n"
                    f"Reason: {reason}\n"
                    f"Total warnings: {warn_count}"
                )
            except discord.Forbidden:
                pass  # DMs closed

            # Escalate based on warn count
            await self._escalate(member, message.guild, warn_count, reason)

    async def _escalate(
        self,
        member: discord.Member,
        guild: discord.Guild,
        warn_count: int,
        reason: str,
    ) -> None:
        """Apply escalating punishments based on warning count."""
        warn_limit = config.DEFAULT_WARN_LIMIT
        timeout_mins = config.DEFAULT_TIMEOUT_MINUTES

        if warn_count >= warn_limit * 2:
            # Ban on extreme repeat offending
            try:
                await guild.ban(member, reason=f"[AutoMod] Exceeded warning limit: {reason}", delete_message_days=1)
                await db_client.log_action(guild.id, "ban", member.id, self.bot.user.id, reason)
            except discord.Forbidden:
                log.warning("Cannot ban %s in %s — missing permissions", member.id, guild.id)

        elif warn_count >= warn_limit:
            # Timeout
            duration = timedelta(minutes=timeout_mins * (warn_count - warn_limit + 1))
            try:
                await member.timeout(duration, reason=f"[AutoMod] Repeated violations: {reason}")
                await db_client.log_action(
                    guild.id, "timeout", member.id, self.bot.user.id,
                    f"{duration} — {reason}"
                )
            except discord.Forbidden:
                log.warning("Cannot timeout %s in %s — missing permissions", member.id, guild.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoMod(bot))
