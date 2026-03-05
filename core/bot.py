"""
KLAUD-NINJA — Core Bot
═══════════════════════════════════════════════════════════════════════════════
Main KlaudBot class. Owns all shared service instances and provides
utility methods used by every cog.

Responsibilities:
  • Boot sequence: DB → AI → cogs → command sync → background tasks
  • Graceful shutdown with connection cleanup
  • License gate helper: assert_licensed()
  • Shared helpers: log_mod_action, increment_warn_count, track_spam
  • Background tasks: cache cleanup, license expiry enforcement
  • Guild join/leave events: welcome message, logging

Services held by this class (injected into all cogs via bot reference):
  bot.db              — DatabaseConnection
  bot.license_manager — LicenseManager
  bot.groq            — GroqService
  bot.settings        — Settings
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import discord
from discord.ext import commands, tasks

from config.settings import Settings
from core.license_manager import LicenseManager
from database.connection import DatabaseConnection
from services.groq_service import GroqService

logger = logging.getLogger("klaud.bot")

# ─── Cog extensions to load at startup ───────────────────────────────────────
COG_EXTENSIONS = [
    "cogs.licensing",
    "cogs.moderation",
    "cogs.admin_ai",
    "cogs.setup_verify",
]


class KlaudBot(commands.Bot):
    """
    KLAUD-NINJA main bot class.

    All cogs access shared services through the bot reference:
        self.bot.db
        self.bot.license_manager
        self.bot.groq
        self.bot.settings
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

        # Configure Discord intents
        intents = discord.Intents.default()
        intents.message_content = True   # Required for moderation
        intents.members = True           # Required for role assignment, kick/ban
        intents.guilds = True
        intents.moderation = True        # Required for timeouts

        super().__init__(
            command_prefix=commands.when_mentioned,   # Prefix = @mention only
            intents=intents,
            owner_id=settings.BOT_OWNER_ID,
            help_command=None,
            case_insensitive=True,
        )

        # ── Service instances ──────────────────────────────────────────────────
        self.db = DatabaseConnection(
            database_url=settings.DATABASE_URL,
            sqlite_path=settings.SQLITE_FALLBACK_PATH,
            pool_min=settings.DB_POOL_MIN_SIZE,
            pool_max=settings.DB_POOL_MAX_SIZE,
        )

        self.license_manager = LicenseManager(
            db=self.db,
            owner_id=settings.BOT_OWNER_ID,
            license_secret=settings.LICENSE_SECRET,
            cache_ttl=float(settings.LICENSE_CACHE_TTL),
            owner_test_server_id=settings.OWNER_TEST_SERVER_ID,
        )

        self.groq = GroqService(
            api_key=settings.AI_API_KEY,
            model=settings.GROQ_MODEL,
            timeout=float(settings.AI_TIMEOUT),
            max_retries=settings.AI_MAX_RETRIES,
        )

        # ── Spam tracking: {guild_id: {user_id: [monotonic_timestamps]}} ──────
        self._spam_tracker: dict[int, dict[int, list[float]]] = {}

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    async def setup_hook(self) -> None:
        """
        Called by discord.py immediately after login, before gateway connection.
        This is where all async initialisation happens.
        """
        logger.info("Running setup_hook...")

        # 1. Connect to database
        await self.db.connect()
        if not self.db.is_available():
            logger.critical(
                "Database is unavailable — bot will run but ALL guilds will be "
                "treated as unlicensed. Fix DATABASE_URL and restart."
            )

        # 2. Initialise Groq AI
        await self.groq.initialise()
        if not self.groq.available:
            logger.warning(
                "Groq AI is unavailable — moderation will use rule-based fallback engine."
            )

        # 3. Load all cogs
        failed_cogs = []
        for extension in COG_EXTENSIONS:
            try:
                await self.load_extension(extension)
                logger.info(f"Loaded cog: {extension}")
            except Exception as exc:
                logger.error(f"Failed to load cog {extension}: {exc}", exc_info=True)
                failed_cogs.append(extension)

        if failed_cogs:
            logger.warning(f"Failed to load {len(failed_cogs)} cog(s): {failed_cogs}")

        # 4. Sync application (slash) commands globally
        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} application commands globally")
        except Exception as exc:
            logger.error(f"Failed to sync commands: {exc}")

        # 5. Start background tasks
        self._cache_cleanup_task.start()
        self._license_expiry_task.start()

        logger.info("setup_hook complete ✓")

    async def close(self) -> None:
        """Graceful shutdown — cancel tasks and close connections."""
        logger.info("KLAUD-NINJA shutting down...")

        # Cancel background tasks
        for task in (self._cache_cleanup_task, self._license_expiry_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Close database pool
        await self.db.close()

        await super().close()
        logger.info("KLAUD-NINJA shutdown complete.")

    # ─── Discord events ───────────────────────────────────────────────────────

    async def on_ready(self) -> None:
        """Called when the bot has finished connecting to Discord."""
        logger.info("=" * 55)
        logger.info(f"  Logged in as: {self.user} (ID: {self.user.id})")
        logger.info(f"  Guilds:       {len(self.guilds)}")
        logger.info(f"  DB mode:      {self.db.mode.name}")
        logger.info(f"  AI (Groq):    {'✓ available' if self.groq.available else '✗ fallback only'}")
        logger.info(f"  AI model:     {self.settings.GROQ_MODEL}")
        logger.info("=" * 55)

        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="for /license redeem",
            )
        )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Send a welcome message when added to a new server."""
        logger.info(f"Joined guild: {guild.name} (ID: {guild.id}, members: {guild.member_count})")

        # Find a channel we can actually send to
        channel = guild.system_channel or next(
            (
                c for c in guild.text_channels
                if c.permissions_for(guild.me).send_messages
            ),
            None,
        )

        if channel:
            embed = discord.Embed(
                title="👋 KLAUD-NINJA has arrived!",
                description=(
                    "Thank you for adding **KLAUD-NINJA** — AI-powered moderation & automation.\n\n"
                    "⚠️ **This bot is license-required.** No features are active until a valid "
                    "license key is redeemed.\n\n"
                    "**To activate this server:**\n"
                    "> Use `/license redeem key:KLAUD-XXXX-XXXX-XXXX`\n\n"
                    "Need a license key? Contact the bot owner."
                ),
                color=discord.Color.blurple(),
            )
            embed.add_field(
                name="📋 License Tiers",
                value=(
                    "🔵 **BASIC** — AI moderation, auto-warn/delete\n"
                    "🟡 **PRO** — Full punishments + AI admin commands\n"
                    "🟣 **ENTERPRISE** — Ban + verification system"
                ),
                inline=False,
            )
            embed.set_footer(text="KLAUD-NINJA • License-Only AI Moderation")
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        logger.info(f"Removed from guild: {guild.name} (ID: {guild.id})")

    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        logger.error(f"Unhandled error in event '{event_method}'", exc_info=True)

    # ─── License gate ─────────────────────────────────────────────────────────

    async def assert_licensed(self, interaction: discord.Interaction) -> bool:
        """
        Verify that the interaction's guild has a valid license.

        Returns True and does nothing if licensed.
        Returns False and sends the unlicensed message if not licensed.
        The bot owner always passes this check.

        Usage in cogs:
            if not await self.bot.assert_licensed(interaction):
                return
        """
        # Bot owner is always allowed
        if interaction.user.id == self.settings.BOT_OWNER_ID:
            return True

        # Must be in a guild
        if not interaction.guild:
            await self._safe_respond(
                interaction,
                "❌ This command can only be used inside a server.",
                ephemeral=True,
            )
            return False

        licensed = await self.license_manager.is_licensed(interaction.guild.id)

        if not licensed:
            await self._safe_respond(
                interaction,
                self.license_manager.UNLICENSED_MESSAGE,
                ephemeral=True,
            )

        return licensed

    # ─── Moderation helpers ───────────────────────────────────────────────────

    async def get_mod_log_channel(
        self, guild: discord.Guild
    ) -> Optional[discord.TextChannel]:
        """
        Find the moderation log channel in a guild.
        Returns None if not found (bot will skip logging silently).
        """
        return discord.utils.get(
            guild.text_channels,
            name=self.settings.MOD_LOG_CHANNEL_NAME,
        )

    async def log_mod_action(
        self,
        guild_id: int,
        user_id: int,
        moderator_id: int,
        action: str,
        reason: str,
        message_content: Optional[str] = None,
        channel_id: Optional[int] = None,
        duration_secs: Optional[int] = None,
        ai_confidence: Optional[float] = None,
        ai_categories: Optional[list[str]] = None,
    ) -> None:
        """
        Persist a moderation action to the database.
        Non-blocking — errors are logged but never raised.
        moderator_id = 0 means the action was taken by the AI.
        """
        try:
            if self.db.is_postgres():
                await self.db.execute(
                    """
                    INSERT INTO mod_actions
                        (guild_id, user_id, moderator_id, action, reason,
                         message_content, channel_id, duration_secs,
                         ai_confidence, ai_categories)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    guild_id, user_id, moderator_id, action, reason,
                    message_content, channel_id, duration_secs,
                    ai_confidence, ai_categories or [],
                )
            elif self.db.is_sqlite():
                cats = ",".join(ai_categories) if ai_categories else None
                await self.db.execute(
                    None,
                    guild_id, user_id, moderator_id, action, reason,
                    message_content, channel_id, duration_secs, ai_confidence, cats,
                    sqlite_query=(
                        "INSERT INTO mod_actions "
                        "(guild_id, user_id, moderator_id, action, reason, "
                        "message_content, channel_id, duration_secs, ai_confidence, ai_categories) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)"
                    ),
                )
        except Exception as exc:
            logger.error(f"Failed to persist mod action: {exc}")

    async def increment_warn_count(
        self, guild_id: int, user_id: int
    ) -> int:
        """
        Increment the warning counter for a user in a guild.
        Uses UPSERT on PostgreSQL. Returns the new warn count.
        """
        try:
            if self.db.is_postgres():
                row = await self.db.fetchrow(
                    """
                    INSERT INTO user_punishments (guild_id, user_id, warn_count, last_action, last_at)
                    VALUES ($1, $2, 1, 'warn', NOW())
                    ON CONFLICT (guild_id, user_id)
                    DO UPDATE SET
                        warn_count  = user_punishments.warn_count + 1,
                        last_action = 'warn',
                        last_at     = NOW()
                    RETURNING warn_count
                    """,
                    guild_id, user_id,
                )
                return int(row["warn_count"]) if row else 1

            elif self.db.is_sqlite():
                row = await self.db.fetchrow(
                    None, guild_id, user_id,
                    sqlite_query=(
                        "SELECT warn_count FROM user_punishments "
                        "WHERE guild_id = ? AND user_id = ?"
                    ),
                )
                if row:
                    new_count = int(row.get("warn_count", 0)) + 1
                    await self.db.execute(
                        None, new_count, guild_id, user_id,
                        sqlite_query=(
                            "UPDATE user_punishments SET warn_count = ? "
                            "WHERE guild_id = ? AND user_id = ?"
                        ),
                    )
                    return new_count
                else:
                    await self.db.execute(
                        None, guild_id, user_id,
                        sqlite_query=(
                            "INSERT INTO user_punishments (guild_id, user_id, warn_count) "
                            "VALUES (?, ?, 1)"
                        ),
                    )
                    return 1

        except Exception as exc:
            logger.error(f"Failed to increment warn count: {exc}")
            return 0

        return 0

    def track_spam(self, guild_id: int, user_id: int) -> int:
        """
        Record a message timestamp for spam detection.
        Returns the number of messages sent within the spam detection window.

        Spam window is configured via SPAM_THRESHOLD_SECONDS.
        """
        now    = time.monotonic()
        window = float(self.settings.SPAM_THRESHOLD_SECONDS)

        guild_data = self._spam_tracker.setdefault(guild_id, {})
        timestamps = guild_data.setdefault(user_id, [])

        # Remove timestamps outside the window
        timestamps[:] = [t for t in timestamps if now - t < window]
        timestamps.append(now)

        return len(timestamps)

    # ─── Interaction helper ───────────────────────────────────────────────────

    @staticmethod
    async def _safe_respond(
        interaction: discord.Interaction,
        content: str,
        ephemeral: bool = True,
        embed: Optional[discord.Embed] = None,
    ) -> None:
        """
        Respond to an interaction safely.
        Handles the case where the interaction has already been responded to
        (uses followup.send instead of response.send_message).
        """
        try:
            kwargs = {"ephemeral": ephemeral}
            if embed:
                kwargs["embed"] = embed
            else:
                kwargs["content"] = content

            if interaction.response.is_done():
                await interaction.followup.send(**kwargs)
            else:
                await interaction.response.send_message(**kwargs)
        except discord.HTTPException as exc:
            logger.debug(f"Could not respond to interaction: {exc}")

    # ─── Background tasks ─────────────────────────────────────────────────────

    @tasks.loop(minutes=10)
    async def _cache_cleanup_task(self) -> None:
        """Periodically purge stale license cache entries to free memory."""
        try:
            removed = await self.license_manager.purge_expired_cache()
            if removed > 0:
                logger.debug(f"Cache cleanup: removed {removed} stale license entries")
        except Exception as exc:
            logger.error(f"Cache cleanup task error: {exc}")

    @tasks.loop(hours=1)
    async def _license_expiry_task(self) -> None:
        """
        Hourly sweep to disable licenses that have passed their expiry date.
        Works on both PostgreSQL and SQLite.
        """
        try:
            if self.db.is_postgres():
                result = await self.db.execute(
                    """
                    UPDATE licenses SET active = FALSE
                    WHERE active = TRUE
                      AND expires_at IS NOT NULL
                      AND expires_at < NOW()
                    """
                )
                logger.debug(f"License expiry sweep: {result}")
            elif self.db.is_sqlite():
                await self.db.execute(
                    None,
                    sqlite_query=(
                        "UPDATE licenses SET active = 0 "
                        "WHERE active = 1 "
                        "AND expires_at IS NOT NULL "
                        "AND expires_at < datetime('now')"
                    ),
                )
        except Exception as exc:
            logger.error(f"License expiry task error: {exc}")

    @_cache_cleanup_task.before_loop
    async def _before_cache_cleanup(self) -> None:
        await self.wait_until_ready()

    @_license_expiry_task.before_loop
    async def _before_expiry_task(self) -> None:
        await self.wait_until_ready()

    def __repr__(self) -> str:
        return (
            f"KlaudBot("
            f"guilds={len(self.guilds)}, "
            f"db={self.db.mode.name}, "
            f"ai={'ok' if self.groq.available else 'fallback'})"
        )
