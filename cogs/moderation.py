"""
KLAUD-NINJA — Moderation Cog
Listens to every message, runs AI analysis, and takes action.
Intensity configurable per-guild: LOW / MEDIUM / HIGH / EXTREME
Punishments: warn → delete → timeout → kick → ban
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.bot import KlaudBot
from services.gemini_service import ModerationAction, ModerationDecision

logger = logging.getLogger("klaud.moderation")

# Minimum confidence threshold before taking action
_MIN_CONFIDENCE: dict[str, float] = {
    "LOW": 0.85,
    "MEDIUM": 0.70,
    "HIGH": 0.55,
    "EXTREME": 0.40,
}

# Tier access gates for punishments
_TIER_MAX_ACTION: dict[str, ModerationAction] = {
    "BASIC": ModerationAction.DELETE,
    "PRO": ModerationAction.KICK,
    "ENTERPRISE": ModerationAction.BAN,
}


class ModerationCog(commands.Cog, name="Moderation"):
    """AI-powered message moderation for all licensed guilds."""

    def __init__(self, bot: KlaudBot) -> None:
        self.bot = bot
        # Track per-guild intensity in memory (also stored in DB)
        self._intensity_cache: dict[int, str] = {}

    # ─── Slash commands ───────────────────────────────────────────────────────

    mod_group = app_commands.Group(
        name="mod",
        description="Moderation settings and tools",
    )

    @mod_group.command(name="intensity", description="Set the AI moderation intensity for this server")
    @app_commands.describe(level="LOW | MEDIUM | HIGH | EXTREME")
    @app_commands.choices(level=[
        app_commands.Choice(name="LOW — Only extreme violations", value="LOW"),
        app_commands.Choice(name="MEDIUM — Balanced (default)", value="MEDIUM"),
        app_commands.Choice(name="HIGH — Strict enforcement", value="HIGH"),
        app_commands.Choice(name="EXTREME — Zero tolerance", value="EXTREME"),
    ])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_intensity(self, interaction: discord.Interaction, level: str) -> None:
        if not await self.bot.assert_licensed(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        level = level.upper()

        # Save to DB
        await self._save_intensity(guild_id, level)
        self._intensity_cache[guild_id] = level

        color_map = {
            "LOW": discord.Color.green(),
            "MEDIUM": discord.Color.blue(),
            "HIGH": discord.Color.orange(),
            "EXTREME": discord.Color.red(),
        }

        embed = discord.Embed(
            title="⚙️ Moderation Intensity Updated",
            description=f"AI moderation level set to **{level}**",
            color=color_map.get(level, discord.Color.blue()),
        )
        descriptions = {
            "LOW": "Only acts on extreme violations like threats, hate speech, and scams.",
            "MEDIUM": "Balanced enforcement. Handles clear toxicity, harassment, and spam.",
            "HIGH": "Strict enforcement. Catches profanity, caps abuse, and invite links.",
            "EXTREME": "Zero tolerance. Acts on anything suspicious or borderline.",
        }
        embed.add_field(name="Description", value=descriptions.get(level, ""), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=False)
        logger.info(f"Intensity set | guild={guild_id} | level={level}")

    @mod_group.command(name="status", description="Show current moderation configuration")
    async def mod_status(self, interaction: discord.Interaction) -> None:
        if not await self.bot.assert_licensed(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        intensity = await self._get_intensity(guild_id)
        tier = await self.bot.license_manager.get_tier(guild_id)
        log_channel = await self.bot.get_mod_log_channel(interaction.guild)

        embed = discord.Embed(
            title="🛡️ Moderation Status",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="License Tier", value=tier, inline=True)
        embed.add_field(name="AI Intensity", value=intensity, inline=True)
        embed.add_field(
            name="Mod Log Channel",
            value=log_channel.mention if log_channel else "Not configured",
            inline=True,
        )
        embed.add_field(
            name="AI Available",
            value="✅ Yes" if self.bot.gemini.available else "⚠️ Fallback (rule-based)",
            inline=True,
        )
        max_action = _TIER_MAX_ACTION.get(tier, ModerationAction.DELETE)
        embed.add_field(
            name="Max Punishment",
            value=max_action.value.upper(),
            inline=True,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @mod_group.command(name="warn", description="Manually warn a user")
    @app_commands.describe(user="User to warn", reason="Reason for warning")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def manual_warn(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str = "No reason provided",
    ) -> None:
        if not await self.bot.assert_licensed(interaction):
            return

        await interaction.response.defer(ephemeral=False)
        await self._execute_warn(user, interaction.guild, reason, moderator_id=interaction.user.id)
        await interaction.followup.send(
            f"⚠️ {user.mention} has been warned. Reason: {reason}"
        )

    @mod_group.command(name="history", description="View moderation history for a user")
    @app_commands.describe(user="User to look up")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def user_history(self, interaction: discord.Interaction, user: discord.Member) -> None:
        if not await self.bot.assert_licensed(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        if self.bot.db.is_postgres():
            rows = await self.bot.db.fetch(
                """
                SELECT action, reason, created_at, ai_confidence
                FROM mod_actions
                WHERE guild_id = $1 AND user_id = $2
                ORDER BY created_at DESC
                LIMIT 10
                """,
                interaction.guild.id, user.id,
            )
        elif self.bot.db.is_sqlite():
            rows = await self.bot.db.fetch(
                None,
                interaction.guild.id, user.id,
                sqlite_query=(
                    "SELECT action, reason, created_at, ai_confidence "
                    "FROM mod_actions WHERE guild_id=? AND user_id=? "
                    "ORDER BY created_at DESC LIMIT 10"
                ),
            )
        else:
            rows = []

        embed = discord.Embed(
            title=f"📋 Mod History: {user.display_name}",
            color=discord.Color.orange(),
        )

        if not rows:
            embed.description = "No moderation history found."
        else:
            lines = []
            for r in rows:
                ts = str(r.get("created_at", ""))[:16]
                action = str(r.get("action", "?")).upper()
                reason = r.get("reason", "")[:60]
                conf = r.get("ai_confidence")
                conf_str = f" ({conf:.0%})" if conf else ""
                lines.append(f"`{ts}` **{action}**{conf_str} — {reason}")
            embed.description = "\n".join(lines)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── Message listener ─────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        Main moderation entry point.
        Called for every message in every server.
        """
        # Ignore DMs, bots, and the owner
        if not message.guild:
            return
        if message.author.bot:
            return
        if message.author.id == self.bot.settings.BOT_OWNER_ID:
            return

        # Gate on license
        licensed = await self.bot.license_manager.is_licensed(message.guild.id)
        if not licensed:
            return

        # Admins and moderators are exempt from auto-mod
        member = message.author
        if isinstance(member, discord.Member):
            if (
                member.guild_permissions.administrator
                or member.guild_permissions.manage_messages
            ):
                return

        # Spam detection (fast, pre-AI)
        msg_count = self.bot.track_spam(message.guild.id, message.author.id)
        intensity = await self._get_intensity(message.guild.id)
        spam_threshold = self.bot.settings.SPAM_THRESHOLD_MESSAGES

        if msg_count >= spam_threshold:
            decision = ModerationDecision(
                action=ModerationAction.TIMEOUT,
                confidence=0.95,
                categories=["spam"],
                reason=f"Spam detected: {msg_count} messages in rapid succession",
                timeout_duration=self.bot.settings.DEFAULT_TIMEOUT_DURATION,
                delete_message=True,
                ai_generated=False,
            )
            await self._apply_decision(message, decision, intensity)
            return

        # Skip very short messages (greetings, reactions) on LOW/MEDIUM
        if len(message.content) < 3 and intensity in ("LOW", "MEDIUM"):
            return

        # AI analysis (async, non-blocking)
        try:
            decision = await asyncio.wait_for(
                self.bot.gemini.analyze_message(
                    content=message.content,
                    intensity=intensity,
                    author_info=f"{message.author} (id={message.author.id})",
                    channel_info=f"#{message.channel.name}",
                ),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Moderation timeout for message in guild {message.guild.id}")
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
        """
        Apply a moderation decision to a message and its author.
        Handles tier gating, permission checks, logging, and notifications.
        """
        guild = message.guild
        member = message.author
        action = decision.action
        min_conf = _MIN_CONFIDENCE.get(intensity, 0.70)

        # Confidence gate
        if decision.confidence < min_conf:
            logger.debug(
                f"Skipping action {action} — confidence {decision.confidence:.2f} < {min_conf}"
            )
            return

        # Tier gate — cap punishment at what the tier allows
        tier = await self.bot.license_manager.get_tier(guild.id)
        max_allowed = _TIER_MAX_ACTION.get(tier, ModerationAction.DELETE)

        action_order = [
            ModerationAction.NONE,
            ModerationAction.WARN,
            ModerationAction.DELETE,
            ModerationAction.TIMEOUT,
            ModerationAction.KICK,
            ModerationAction.BAN,
        ]
        action_idx = action_order.index(action)
        max_idx = action_order.index(max_allowed)
        if action_idx > max_idx:
            action = max_allowed
            logger.debug(f"Action capped at {action} for tier {tier}")

        if action == ModerationAction.NONE:
            return

        # Delete the message if required
        if decision.delete_message or action in (
            ModerationAction.DELETE, ModerationAction.TIMEOUT,
            ModerationAction.KICK, ModerationAction.BAN,
        ):
            try:
                await message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass

        # Execute the punishment
        executed_action = action
        try:
            if action == ModerationAction.WARN:
                warn_count = await self._execute_warn(member, guild, decision.reason)
                # Escalate after 3 warnings
                if warn_count >= 3 and tier in ("PRO", "ENTERPRISE"):
                    executed_action = ModerationAction.TIMEOUT
                    await self._execute_timeout(member, guild, decision.reason, decision.timeout_duration)

            elif action == ModerationAction.DELETE:
                # Message already deleted above — just warn
                await self._execute_warn(member, guild, decision.reason)

            elif action == ModerationAction.TIMEOUT:
                await self._execute_timeout(member, guild, decision.reason, decision.timeout_duration)

            elif action == ModerationAction.KICK:
                await self._execute_kick(member, guild, decision.reason)

            elif action == ModerationAction.BAN:
                await self._execute_ban(member, guild, decision.reason)

        except discord.Forbidden:
            logger.warning(
                f"Missing permissions to execute {action} on {member} in {guild.name}"
            )
        except Exception as e:
            logger.error(f"Failed to execute {action} on {member}: {e}")

        # Log to mod channel
        await self._send_mod_log(message, decision, executed_action, tier)

        # Save to database
        await self.bot.log_mod_action(
            guild_id=guild.id,
            user_id=member.id,
            moderator_id=0,  # 0 = AI
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

    async def _execute_warn(
        self,
        member: discord.Member,
        guild: discord.Guild,
        reason: str,
        moderator_id: int = 0,
    ) -> int:
        """Send a warning DM and return the new warn count."""
        warn_count = await self.bot.increment_warn_count(guild.id, member.id)

        try:
            embed = discord.Embed(
                title="⚠️ Warning Received",
                description=f"You have received a warning in **{guild.name}**.",
                color=discord.Color.yellow(),
            )
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.add_field(name="Warning #", value=str(warn_count), inline=True)
            embed.set_footer(text="Continued violations may result in stronger action.")
            await member.send(embed=embed)
        except discord.Forbidden:
            pass  # DMs closed

        return warn_count

    async def _execute_timeout(
        self,
        member: discord.Member,
        guild: discord.Guild,
        reason: str,
        duration_secs: int,
    ) -> None:
        """Timeout (mute) a member for a given duration."""
        until = discord.utils.utcnow() + timedelta(seconds=duration_secs)
        await member.timeout(until, reason=f"[KLAUD AI] {reason}"[:512])

        try:
            embed = discord.Embed(
                title="⏱️ You Have Been Timed Out",
                description=f"You have been timed out in **{guild.name}**.",
                color=discord.Color.orange(),
            )
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.add_field(name="Duration", value=f"{duration_secs // 60} minutes", inline=True)
            await member.send(embed=embed)
        except discord.Forbidden:
            pass

    async def _execute_kick(
        self,
        member: discord.Member,
        guild: discord.Guild,
        reason: str,
    ) -> None:
        """Kick a member from the guild."""
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
        member: discord.Member,
        guild: discord.Guild,
        reason: str,
    ) -> None:
        """Ban a member from the guild."""
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

    # ─── Mod log embed ────────────────────────────────────────────────────────

    async def _send_mod_log(
        self,
        message: discord.Message,
        decision: ModerationDecision,
        action: ModerationAction,
        tier: str,
    ) -> None:
        """Send a rich embed to the mod-log channel."""
        log_channel = await self.bot.get_mod_log_channel(message.guild)
        if not log_channel:
            return

        color_map = {
            ModerationAction.WARN: discord.Color.yellow(),
            ModerationAction.DELETE: discord.Color.orange(),
            ModerationAction.TIMEOUT: discord.Color.orange(),
            ModerationAction.KICK: discord.Color.red(),
            ModerationAction.BAN: discord.Color.dark_red(),
        }
        action_emoji = {
            ModerationAction.WARN: "⚠️",
            ModerationAction.DELETE: "🗑️",
            ModerationAction.TIMEOUT: "⏱️",
            ModerationAction.KICK: "🦶",
            ModerationAction.BAN: "🔨",
        }

        embed = discord.Embed(
            title=f"{action_emoji.get(action, '🛡️')} Auto-Mod Action: {action.value.upper()}",
            color=color_map.get(action, discord.Color.blurple()),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=message.author.display_avatar.url)
        embed.add_field(name="User", value=f"{message.author.mention} (`{message.author.id}`)", inline=True)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        embed.add_field(name="Action", value=action.value.upper(), inline=True)
        embed.add_field(name="Reason", value=decision.reason[:200], inline=False)

        if decision.categories:
            embed.add_field(
                name="Categories",
                value=", ".join(decision.categories),
                inline=True,
            )

        embed.add_field(
            name="Confidence",
            value=f"{decision.confidence:.0%}",
            inline=True,
        )
        embed.add_field(
            name="AI",
            value="Gemini" if decision.ai_generated else "Fallback",
            inline=True,
        )

        if message.content:
            content_preview = message.content[:300]
            if len(message.content) > 300:
                content_preview += "..."
            embed.add_field(name="Message", value=f"```{content_preview}```", inline=False)

        embed.set_footer(text=f"KLAUD-NINJA • Tier: {tier}")

        try:
            await log_channel.send(embed=embed)
        except discord.Forbidden:
            logger.warning(f"Cannot write to mod-log channel in guild {message.guild.id}")
        except Exception as e:
            logger.error(f"Error sending mod log: {e}")

    # ─── Intensity helpers ────────────────────────────────────────────────────

    async def _get_intensity(self, guild_id: int) -> str:
        """Get the moderation intensity for a guild (cached)."""
        if guild_id in self._intensity_cache:
            return self._intensity_cache[guild_id]

        # Try DB
        try:
            if self.bot.db.is_postgres():
                row = await self.bot.db.fetchrow(
                    "SELECT mod_intensity FROM guild_settings WHERE guild_id = $1",
                    guild_id,
                )
            elif self.bot.db.is_sqlite():
                row = await self.bot.db.fetchrow(
                    None,
                    guild_id,
                    sqlite_query="SELECT mod_intensity FROM guild_settings WHERE guild_id = ?",
                )
            else:
                row = None

            intensity = row["mod_intensity"] if row else "MEDIUM"
        except Exception:
            intensity = "MEDIUM"

        self._intensity_cache[guild_id] = intensity
        return intensity

    async def _save_intensity(self, guild_id: int, intensity: str) -> None:
        """Persist intensity setting to the database."""
        try:
            if self.bot.db.is_postgres():
                await self.bot.db.execute(
                    """
                    INSERT INTO guild_settings (guild_id, mod_intensity)
                    VALUES ($1, $2)
                    ON CONFLICT (guild_id) DO UPDATE SET mod_intensity = $2, updated_at = NOW()
                    """,
                    guild_id, intensity,
                )
            elif self.bot.db.is_sqlite():
                existing = await self.bot.db.fetchrow(
                    None, guild_id,
                    sqlite_query="SELECT guild_id FROM guild_settings WHERE guild_id = ?",
                )
                if existing:
                    await self.bot.db.execute(
                        None, intensity, guild_id,
                        sqlite_query="UPDATE guild_settings SET mod_intensity=? WHERE guild_id=?",
                    )
                else:
                    await self.bot.db.execute(
                        None, guild_id, intensity,
                        sqlite_query="INSERT INTO guild_settings (guild_id, mod_intensity) VALUES (?,?)",
                    )
        except Exception as e:
            logger.error(f"Failed to save intensity for guild {guild_id}: {e}")


async def setup(bot: KlaudBot) -> None:
    await bot.add_cog(ModerationCog(bot))
