"""
KLAUD-NINJA — Moderation Cog
═══════════════════════════════════════════════════════════════════════════════
AI-powered message moderation using Groq (llama-3.3-70b-versatile).
Falls back to rule-based engine if Groq is unavailable.

Enforcement pipeline per message:
  1. License check       — skip if server unlicensed
  2. Exemption check     — skip if author is admin/moderator/bot
  3. Spam pre-check      — detect rapid posting before AI call
  4. Groq AI analysis    — structured JSON response with action + confidence
  5. Confidence gate     — minimum threshold per intensity level
  6. Tier gate           — cap punishment at tier's maximum allowed action
  7. Execute action      — warn / delete / timeout / kick / ban
  8. DM notification     — inform user of action taken
  9. Mod log embed       — post to #klaud-mod-log channel
  10. DB persistence     — store in mod_actions table

Intensity levels:
  LOW     — Only act on extreme violations (threats, hate speech, scams)
  MEDIUM  — Act on clear violations (default)
  HIGH    — Act on mild violations too
  EXTREME — Zero tolerance

Tier action caps:
  BASIC      — Max: DELETE (no kick/ban)
  PRO        — Max: KICK
  ENTERPRISE — Max: BAN

Slash commands:
  /mod intensity <level>  — Set moderation intensity
  /mod status             — Show current mod configuration and AI stats
  /mod warn <user>        — Manual warning
  /mod history <user>     — View last 10 actions for a user
═══════════════════════════════════════════════════════════════════════════════
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

# ─── Configuration ────────────────────────────────────────────────────────────

# Minimum AI confidence required to act, by intensity level
_MIN_CONFIDENCE: dict[str, float] = {
    "LOW":     0.88,
    "MEDIUM":  0.72,
    "HIGH":    0.55,
    "EXTREME": 0.38,
}

# Maximum action allowed per tier
_TIER_MAX_ACTION: dict[str, ModerationAction] = {
    "BASIC":      ModerationAction.DELETE,
    "PRO":        ModerationAction.KICK,
    "ENTERPRISE": ModerationAction.BAN,
    "NONE":       ModerationAction.WARN,
}

# Action escalation order (higher index = more severe)
_ACTION_ORDER = [
    ModerationAction.NONE,
    ModerationAction.WARN,
    ModerationAction.DELETE,
    ModerationAction.TIMEOUT,
    ModerationAction.KICK,
    ModerationAction.BAN,
]

# Warn count thresholds for automatic escalation
_WARN_ESCALATE_TIMEOUT = 3    # Warns before auto-timeout
_WARN_ESCALATE_KICK    = 5    # Warns before auto-kick

# Color coding for mod log embeds
_ACTION_COLORS: dict[str, int] = {
    "warn":    0xFFA500,    # Orange
    "delete":  0xFF6B35,    # Dark orange
    "timeout": 0xFF4444,    # Red
    "kick":    0xDD2222,    # Dark red
    "ban":     0x990000,    # Very dark red
}


# ─── Cog ─────────────────────────────────────────────────────────────────────

class ModerationCog(commands.Cog, name="Moderation"):
    """
    AI-powered message moderation using Groq.
    Monitors every message in licensed servers and enforces server policies.
    """

    def __init__(self, bot: KlaudBot) -> None:
        self.bot = bot
        # In-memory intensity storage: guild_id → intensity string
        # Persisted to guild_settings table on change
        self._intensity: dict[int, str] = {}
        logger.info("ModerationCog initialised")

    # ─── Message listener ────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        Primary moderation listener. Runs on every message in every guild.
        Bails out early if any of the following are true:
          - Message is from a bot
          - Message is a DM (no guild)
          - Server is not licensed
          - Author has Manage Messages or Administrator permission (moderator)
          - Message is too short to analyze (< 3 characters)
        """
        # ── Quick exits ───────────────────────────────────────────────────────

        if message.author.bot:
            return

        if not message.guild:
            return

        if len(message.content) < 3:
            return

        # ── License gate ──────────────────────────────────────────────────────

        if not await self.bot.license_manager.is_licensed(message.guild.id):
            return

        # ── Moderator exemption ───────────────────────────────────────────────

        member = message.guild.get_member(message.author.id)
        if member and (
            member.guild_permissions.manage_messages
            or member.guild_permissions.administrator
        ):
            return

        # ── Get moderation intensity ──────────────────────────────────────────

        intensity = await self._get_intensity(message.guild.id)

        # ── Spam pre-check (before AI call — saves API quota) ─────────────────

        msg_count = self.bot.track_spam(message.guild.id, message.author.id)
        spam_threshold = self.bot.settings.SPAM_THRESHOLD_MESSAGES

        if msg_count >= spam_threshold:
            logger.debug(
                f"Spam detected | guild={message.guild.id} | "
                f"user={message.author.id} | count={msg_count}"
            )
            decision = ModerationDecision(
                action=ModerationAction.TIMEOUT,
                confidence=0.92,
                categories=["spam"],
                reason=f"Sending {msg_count} messages in rapid succession",
                timeout_duration=self.bot.settings.DEFAULT_TIMEOUT_DURATION,
                ai_generated=False,
            )
            await self._apply_decision(message, decision, intensity)
            return

        # ── AI analysis ───────────────────────────────────────────────────────

        author_context = (
            f"username={message.author.name}, "
            f"account_age_days={(discord.utils.utcnow() - message.author.created_at).days}"
        )
        channel_context = f"channel=#{message.channel.name}"

        try:
            decision = await asyncio.wait_for(
                self.bot.groq.analyze_message(
                    content=message.content,
                    intensity=intensity,
                    author_info=author_context,
                    channel_info=channel_context,
                ),
                timeout=self.bot.settings.AI_TIMEOUT + 2.0,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"AI analysis timed out | guild={message.guild.id} | "
                f"channel=#{message.channel.name}"
            )
            # Fallback to rule-based
            from services.ai_fallback import FallbackModerator
            decision = FallbackModerator.analyze(message.content, intensity)
        except Exception as exc:
            logger.error(f"AI analysis error: {exc}", exc_info=True)
            return

        # ── Apply the decision ────────────────────────────────────────────────

        if decision.action != ModerationAction.NONE:
            await self._apply_decision(message, decision, intensity)

    # ─── Decision application ────────────────────────────────────────────────

    async def _apply_decision(
        self,
        message: discord.Message,
        decision: ModerationDecision,
        intensity: str,
    ) -> None:
        """
        Apply a moderation decision to a message and its author.

        Steps:
          1. Confidence gate — abort if below threshold
          2. Tier gate — cap action at tier maximum
          3. Warn escalation — auto-upgrade action if too many warnings
          4. Execute the action
          5. DM the user
          6. Post to mod log
          7. Persist to database
        """
        guild  = message.guild
        author = message.author
        member = guild.get_member(author.id)

        if not member:
            return

        # ── 1. Confidence gate ────────────────────────────────────────────────

        min_conf = _MIN_CONFIDENCE.get(intensity.upper(), 0.72)
        if decision.confidence < min_conf and decision.ai_generated:
            logger.debug(
                f"Decision below confidence threshold | "
                f"confidence={decision.confidence:.2f} < min={min_conf:.2f} | "
                f"action={decision.action.value}"
            )
            return

        # ── 2. Tier gate ──────────────────────────────────────────────────────

        tier = await self.bot.license_manager.get_tier(guild.id)
        max_action = _TIER_MAX_ACTION.get(tier, ModerationAction.WARN)

        # Cap the action at tier maximum
        action = decision.action
        if _ACTION_ORDER.index(action) > _ACTION_ORDER.index(max_action):
            logger.debug(
                f"Action capped by tier | original={action.value} | "
                f"max={max_action.value} | tier={tier}"
            )
            action = max_action

        # ── 3. Warn escalation ────────────────────────────────────────────────

        if action == ModerationAction.WARN:
            warn_count = await self.bot.increment_warn_count(guild.id, member.id)

            # Escalate based on warn count if tier allows
            if warn_count >= _WARN_ESCALATE_KICK and max_action >= ModerationAction.KICK:
                action = ModerationAction.KICK
                decision.reason += f" (auto-escalated: {warn_count} warnings)"
            elif warn_count >= _WARN_ESCALATE_TIMEOUT:
                action = ModerationAction.TIMEOUT
                decision.reason += f" (auto-escalated: {warn_count} warnings)"

        # ── 4. Delete message ─────────────────────────────────────────────────

        if decision.delete_message or action in (
            ModerationAction.DELETE,
            ModerationAction.TIMEOUT,
            ModerationAction.KICK,
            ModerationAction.BAN,
        ):
            try:
                await message.delete()
                logger.debug(f"Message deleted | guild={guild.id} | msg={message.id}")
            except discord.HTTPException as exc:
                logger.warning(f"Failed to delete message: {exc}")

        # ── 5. Execute punishment ─────────────────────────────────────────────

        success = await self._execute_punishment(
            member=member,
            action=action,
            reason=decision.reason,
            timeout_duration=decision.timeout_duration,
        )

        if not success:
            return

        # ── 6. DM notification ────────────────────────────────────────────────

        await self._notify_user(
            member=member,
            action=action,
            reason=decision.reason,
            guild_name=guild.name,
        )

        # ── 7. Mod log embed ──────────────────────────────────────────────────

        await self._post_mod_log(
            guild=guild,
            member=member,
            action=action,
            reason=decision.reason,
            original_message=message.content,
            channel=message.channel,
            decision=decision,
        )

        # ── 8. Database logging ───────────────────────────────────────────────

        await self.bot.log_mod_action(
            guild_id=guild.id,
            user_id=member.id,
            moderator_id=0,   # 0 = AI
            action=action.value,
            reason=decision.reason,
            message_content=message.content[:500],
            channel_id=message.channel.id,
            duration_secs=decision.timeout_duration if action == ModerationAction.TIMEOUT else None,
            ai_confidence=decision.confidence,
            ai_categories=decision.categories,
        )

        logger.info(
            f"Moderation action | guild={guild.id} | user={member.id} | "
            f"action={action.value} | confidence={decision.confidence:.2f} | "
            f"categories={decision.categories} | ai={decision.ai_generated}"
        )

    # ─── Punishment execution ─────────────────────────────────────────────────

    async def _execute_punishment(
        self,
        member: discord.Member,
        action: ModerationAction,
        reason: str,
        timeout_duration: int = 600,
    ) -> bool:
        """
        Execute the actual Discord punishment.
        Returns True on success, False if the action failed or was skipped.
        """
        if action == ModerationAction.NONE or action == ModerationAction.DELETE:
            return True   # No punishment needed beyond deletion

        audit_reason = f"KLAUD AI: {reason[:200]}"

        try:
            if action == ModerationAction.WARN:
                # Warning is tracked in DB — no Discord action needed here
                return True

            elif action == ModerationAction.TIMEOUT:
                duration = timedelta(seconds=max(60, min(timeout_duration, 2419200)))
                await member.timeout(duration, reason=audit_reason)

            elif action == ModerationAction.KICK:
                await member.kick(reason=audit_reason)

            elif action == ModerationAction.BAN:
                await member.ban(
                    reason=audit_reason,
                    delete_message_days=1,
                )

            return True

        except discord.Forbidden:
            logger.warning(
                f"Missing permissions to {action.value} "
                f"{member.display_name} (ID: {member.id})"
            )
            return False
        except discord.HTTPException as exc:
            logger.error(f"Failed to execute {action.value}: {exc}")
            return False

    # ─── User DM notification ─────────────────────────────────────────────────

    async def _notify_user(
        self,
        member: discord.Member,
        action: ModerationAction,
        reason: str,
        guild_name: str,
    ) -> None:
        """
        Send a DM to the moderated user explaining what happened.
        Silently fails if DMs are disabled.
        """
        action_descriptions = {
            ModerationAction.WARN:    "⚠️ received a warning in",
            ModerationAction.DELETE:  "had a message deleted in",
            ModerationAction.TIMEOUT: "🔇 been timed out in",
            ModerationAction.KICK:    "👢 been kicked from",
            ModerationAction.BAN:     "🔨 been banned from",
        }

        description = action_descriptions.get(action, "received a moderation action in")

        embed = discord.Embed(
            title="⚖️ KLAUD Moderation Action",
            description=f"You have {description} **{guild_name}**.",
            color=_ACTION_COLORS.get(action.value, 0xFF6B35),
        )
        embed.add_field(name="Action", value=action.value.title(), inline=True)
        embed.add_field(name="Reason", value=reason[:500], inline=False)
        embed.set_footer(
            text="This action was taken automatically by KLAUD AI. "
                 "Contact the server moderators if you believe this is an error."
        )

        try:
            await member.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass   # User has DMs disabled — silently skip

    # ─── Mod log posting ──────────────────────────────────────────────────────

    async def _post_mod_log(
        self,
        guild: discord.Guild,
        member: discord.Member,
        action: ModerationAction,
        reason: str,
        original_message: str,
        channel: discord.TextChannel,
        decision: ModerationDecision,
    ) -> None:
        """Post a detailed moderation embed to the mod log channel."""
        log_channel = await self.bot.get_mod_log_channel(guild)
        if not log_channel:
            return

        embed = discord.Embed(
            title=f"🛡️ KLAUD Moderation — {action.value.upper()}",
            color=_ACTION_COLORS.get(action.value, 0xFF6B35),
        )
        embed.add_field(name="User", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Action", value=action.value.title(), inline=True)
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="Reason", value=reason[:500], inline=False)

        if original_message:
            truncated = original_message[:300] + "..." if len(original_message) > 300 else original_message
            embed.add_field(name="Original Message", value=f"```{truncated}```", inline=False)

        if decision.categories:
            embed.add_field(
                name="Detected Categories",
                value=", ".join(decision.categories),
                inline=True,
            )
        embed.add_field(
            name="Confidence",
            value=f"{decision.confidence:.0%}",
            inline=True,
        )
        embed.add_field(
            name="Engine",
            value="🤖 Groq AI" if decision.ai_generated else "📋 Rule-based",
            inline=True,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"User ID: {member.id}")
        embed.timestamp = discord.utils.utcnow()

        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException as exc:
            logger.warning(f"Failed to post mod log: {exc}")

    # ─── Intensity management ────────────────────────────────────────────────

    async def _get_intensity(self, guild_id: int) -> str:
        """Get the moderation intensity for a guild (cached in memory, defaults to MEDIUM)."""
        if guild_id in self._intensity:
            return self._intensity[guild_id]

        # Try loading from database
        try:
            if self.bot.db.is_postgres():
                row = await self.bot.db.fetchrow(
                    "SELECT mod_intensity FROM guild_settings WHERE guild_id = $1",
                    guild_id,
                )
            else:
                row = await self.bot.db.fetchrow(
                    None, guild_id,
                    sqlite_query="SELECT mod_intensity FROM guild_settings WHERE guild_id = ?",
                )

            if row and row.get("mod_intensity"):
                intensity = str(row["mod_intensity"]).upper()
                self._intensity[guild_id] = intensity
                return intensity
        except Exception as exc:
            logger.error(f"Failed to load intensity for guild {guild_id}: {exc}")

        self._intensity[guild_id] = "MEDIUM"
        return "MEDIUM"

    async def _set_intensity(self, guild_id: int, intensity: str) -> None:
        """Persist moderation intensity for a guild."""
        self._intensity[guild_id] = intensity

        try:
            if self.bot.db.is_postgres():
                await self.bot.db.execute(
                    """
                    INSERT INTO guild_settings (guild_id, mod_intensity, updated_at)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT (guild_id)
                    DO UPDATE SET mod_intensity = $2, updated_at = NOW()
                    """,
                    guild_id, intensity,
                )
            else:
                existing = await self.bot.db.fetchrow(
                    None, guild_id,
                    sqlite_query="SELECT guild_id FROM guild_settings WHERE guild_id = ?",
                )
                if existing:
                    await self.bot.db.execute(
                        None, intensity, guild_id,
                        sqlite_query="UPDATE guild_settings SET mod_intensity = ? WHERE guild_id = ?",
                    )
                else:
                    await self.bot.db.execute(
                        None, guild_id, intensity,
                        sqlite_query="INSERT INTO guild_settings (guild_id, mod_intensity) VALUES (?, ?)",
                    )
        except Exception as exc:
            logger.error(f"Failed to save intensity for guild {guild_id}: {exc}")

    # ─── Slash commands ───────────────────────────────────────────────────────

    mod_group = app_commands.Group(
        name="mod",
        description="Moderation configuration commands",
    )

    @mod_group.command(
        name="intensity",
        description="Set the AI moderation intensity level",
    )
    @app_commands.describe(level="Intensity: LOW / MEDIUM / HIGH / EXTREME")
    @app_commands.choices(level=[
        app_commands.Choice(name="LOW — Extreme violations only",    value="LOW"),
        app_commands.Choice(name="MEDIUM — Clear violations (default)", value="MEDIUM"),
        app_commands.Choice(name="HIGH — Mild violations",           value="HIGH"),
        app_commands.Choice(name="EXTREME — Zero tolerance",         value="EXTREME"),
    ])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def mod_intensity(
        self,
        interaction: discord.Interaction,
        level: str,
    ) -> None:
        """Set the AI moderation intensity for this server."""
        if not await self.bot.assert_licensed(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        level = level.upper()
        await self._set_intensity(interaction.guild.id, level)

        descriptions = {
            "LOW":     "Only extreme violations (threats, hate speech, scams) will trigger action.",
            "MEDIUM":  "Clear violations will be actioned. Mild language is allowed.",
            "HIGH":    "Profanity, caps abuse, invite links, and toxic messages will be moderated.",
            "EXTREME": "Zero tolerance — borderline content will be actioned.",
        }

        embed = discord.Embed(
            title="⚙️ Moderation Intensity Updated",
            description=f"Intensity set to **{level}**.\n\n{descriptions.get(level, '')}",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Confidence Threshold",
            value=f"{_MIN_CONFIDENCE.get(level, 0.72):.0%}",
            inline=True,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @mod_group.command(
        name="status",
        description="View current moderation configuration and AI health",
    )
    async def mod_status(self, interaction: discord.Interaction) -> None:
        """Show moderation status, intensity, and Groq AI health."""
        if not await self.bot.assert_licensed(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        intensity   = await self._get_intensity(interaction.guild.id)
        tier        = await self.bot.license_manager.get_tier(interaction.guild.id)
        ai_stats    = self.bot.groq.stats()
        log_channel = await self.bot.get_mod_log_channel(interaction.guild)

        embed = discord.Embed(
            title="🛡️ KLAUD Moderation Status",
            color=discord.Color.blurple(),
        )

        # License
        embed.add_field(name="License Tier", value=tier, inline=True)
        embed.add_field(name="Intensity",    value=intensity, inline=True)
        embed.add_field(
            name="Max Action",
            value=_TIER_MAX_ACTION.get(tier, ModerationAction.WARN).value.title(),
            inline=True,
        )

        # AI
        ai_status = "✅ Groq AI (online)" if ai_stats["available"] else "⚠️ Rule-based fallback"
        embed.add_field(name="AI Engine", value=ai_status, inline=True)
        embed.add_field(name="Model",     value=ai_stats["model"], inline=True)
        embed.add_field(
            name="AI Stats",
            value=(
                f"Calls: {ai_stats['total_calls']} | "
                f"Errors: {ai_stats['total_errors']} | "
                f"Error rate: {ai_stats['error_rate']:.1%}"
            ),
            inline=False,
        )

        # Mod log
        embed.add_field(
            name="Mod Log Channel",
            value=log_channel.mention if log_channel else "❌ Not found — create #klaud-mod-log",
            inline=False,
        )

        embed.set_footer(text="Use /mod intensity to change the enforcement level")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @mod_group.command(
        name="warn",
        description="Manually warn a user",
    )
    @app_commands.describe(
        user="The user to warn",
        reason="Reason for the warning",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def mod_warn(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str = "No reason provided",
    ) -> None:
        """Manually warn a user. Increments their warning counter."""
        if not await self.bot.assert_licensed(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        warn_count = await self.bot.increment_warn_count(interaction.guild.id, user.id)

        # Notify the user
        embed_dm = discord.Embed(
            title="⚠️ Warning Received",
            description=f"You have received a warning in **{interaction.guild.name}**.",
            color=0xFFA500,
        )
        embed_dm.add_field(name="Reason", value=reason, inline=False)
        embed_dm.add_field(name="Warning Count", value=str(warn_count), inline=True)
        embed_dm.set_footer(text="Please review the server rules.")
        try:
            await user.send(embed=embed_dm)
        except discord.HTTPException:
            pass

        # Post to mod log
        await self._post_mod_log(
            guild=interaction.guild,
            member=user,
            action=ModerationAction.WARN,
            reason=f"[Manual] {reason}",
            original_message="",
            channel=interaction.channel,
            decision=ModerationDecision(
                action=ModerationAction.WARN,
                confidence=1.0,
                reason=f"[Manual] {reason}",
                ai_generated=False,
            ),
        )

        # DB log
        await self.bot.log_mod_action(
            guild_id=interaction.guild.id,
            user_id=user.id,
            moderator_id=interaction.user.id,
            action="warn",
            reason=f"[Manual] {reason}",
        )

        embed = discord.Embed(
            title="⚠️ Warning Issued",
            description=f"{user.mention} has been warned.\nTotal warnings: **{warn_count}**",
            color=0xFFA500,
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @mod_group.command(
        name="history",
        description="View moderation history for a user",
    )
    @app_commands.describe(user="The user to check")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def mod_history(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        """Show the last 10 moderation actions for a user in this server."""
        if not await self.bot.assert_licensed(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        try:
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
            else:
                rows = await self.bot.db.fetch(
                    None, interaction.guild.id, user.id,
                    sqlite_query=(
                        "SELECT action, reason, created_at, ai_confidence "
                        "FROM mod_actions "
                        "WHERE guild_id = ? AND user_id = ? "
                        "ORDER BY created_at DESC LIMIT 10"
                    ),
                )
        except Exception as exc:
            await interaction.followup.send(f"❌ Database error: {exc}", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"📋 Moderation History — {user.display_name}",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=user.display_avatar.url)

        if not rows:
            embed.description = "No moderation history found for this user."
        else:
            lines = []
            for row in rows:
                ts  = row.get("created_at", "?")
                act = str(row.get("action", "?")).upper()
                rsn = str(row.get("reason", "?"))[:60]
                conf = row.get("ai_confidence")
                conf_str = f" ({conf:.0%})" if conf else ""
                if hasattr(ts, "strftime"):
                    ts_str = ts.strftime("%Y-%m-%d %H:%M")
                else:
                    ts_str = str(ts)[:16]
                lines.append(f"`{ts_str}` **{act}**{conf_str} — {rsn}")

            embed.description = "\n".join(lines)

        embed.set_footer(text=f"User ID: {user.id}")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: KlaudBot) -> None:
    await bot.add_cog(ModerationCog(bot))
