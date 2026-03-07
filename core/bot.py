"""
KLAUD-NINJA — Core Bot
Main bot class. Manages startup, cog loading, event routing,
and holds shared service references used by all cogs.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import discord
from discord.ext import commands, tasks

from config.settings import Settings
from core.license_manager import LicenseManager
from database.connection import DatabaseConnection
from services.groq_service import GroqService
from utils.smart_logger import setup_logging

logger = logging.getLogger("klaud.bot")

# ─── Cogs to load ────────────────────────────────────────────────────────────
COG_EXTENSIONS = [
    "cogs.licensing",
    "cogs.moderation",
    "cogs.admin_ai",
    "cogs.setup_verify",
]


class KlaudBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        intents.moderation = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            owner_id=settings.BOT_OWNER_ID,
            help_command=None,
            case_insensitive=True,
        )

        self.db: DatabaseConnection = DatabaseConnection(
            database_url=settings.DATABASE_URL,
            sqlite_path=settings.SQLITE_FALLBACK_PATH,
            pool_min=settings.DB_POOL_MIN_SIZE,
            pool_max=settings.DB_POOL_MAX_SIZE,
        )

        self.license_manager: LicenseManager = LicenseManager(
            db=self.db,
            owner_id=settings.BOT_OWNER_ID,
            license_secret=settings.LICENSE_SECRET,
            cache_ttl=float(settings.LICENSE_CACHE_TTL),
            owner_test_server_id=settings.OWNER_TEST_SERVER_ID,
        )

        self.groq: GroqService = GroqService(
            api_key=settings.AI_API_KEY,
            model=settings.GROQ_MODEL,
            timeout=float(settings.AI_TIMEOUT),
            max_retries=settings.AI_MAX_RETRIES,
        )

        self._spam_tracker: dict[int, dict[int, list[float]]] = {}

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    async def setup_hook(self) -> None:
        logger.info("Running setup_hook...")

        await self.db.connect()
        if not self.db.is_available():
            logger.critical("Database unavailable — bot will run with all guilds denied")

        await self.groq.initialise()

        for extension in COG_EXTENSIONS:
            try:
                await self.load_extension(extension)
                logger.info(f"Loaded cog: {extension}")
            except Exception as e:
                logger.error(f"Failed to load cog {extension}: {e}", exc_info=True)

        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} application commands globally")
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")

        self._cache_cleanup_loop.start()
        self._license_expiry_check.start()

    async def close(self) -> None:
        logger.info("Shutting down KLAUD...")
        self._cache_cleanup_loop.cancel()
        self._license_expiry_check.cancel()
        await self.db.close()
        await super().close()

    # ─── Events ───────────────────────────────────────────────────────────────

    async def on_ready(self) -> None:
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guilds")
        logger.info(f"Database mode: {self.db.mode.name}")
        logger.info(f"AI available: {self.groq.available}")

        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="for /license redeem",
            )
        )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        logger.info(f"Joined guild: {guild.name} (ID: {guild.id})")
        channel = guild.system_channel or next(
            (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages),
            None,
        )
        if channel:
            embed = discord.Embed(
                title="👋 KLAUD-NINJA has arrived!",
                description=(
                    "Thank you for adding **KLAUD-NINJA**!\n\n"
                    "This bot is **license-required**. No features are available until "
                    "a valid license is redeemed.\n\n"
                    "Use `/license redeem <key>` to activate this server.\n\n"
                    "Need a license? Contact the bot owner."
                ),
                color=discord.Color.blurple(),
            )
            embed.set_footer(text="KLAUD-NINJA • License-Only AI Moderation")
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        logger.info(f"Removed from guild: {guild.name} (ID: {guild.id})")

    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        logger.error(f"Unhandled error in event {event_method}", exc_info=True)

    async def on_application_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, discord.app_commands.MissingPermissions):
            await self._safe_respond(interaction, "❌ You don't have permission to use this command.", ephemeral=True)
        elif isinstance(error, discord.app_commands.CommandOnCooldown):
            await self._safe_respond(interaction, f"⏱️ Slow down! Try again in {error.retry_after:.1f} seconds.", ephemeral=True)
        else:
            logger.error(f"App command error: {error}", exc_info=True)
            await self._safe_respond(interaction, "❌ An unexpected error occurred. Please try again.", ephemeral=True)

    # ─── Utility methods ──────────────────────────────────────────────────────

    async def assert_licensed(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.settings.BOT_OWNER_ID:
            return True
        if not interaction.guild:
            await self._safe_respond(interaction, "❌ This command can only be used in a server.", ephemeral=True)
            return False
        licensed = await self.license_manager.is_licensed(interaction.guild.id)
        if not licensed:
            await self._safe_respond(interaction, self.license_manager.UNLICENSED_MESSAGE, ephemeral=True)
        return licensed

    async def get_mod_log_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        name = self.settings.MOD_LOG_CHANNEL_NAME
        return discord.utils.get(guild.text_channels, name=name)

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
        try:
            if self.db.is_postgres():
                await self.db.execute(
                    "INSERT INTO mod_actions (guild_id, user_id, moderator_id, action, reason, "
                    "message_content, channel_id, duration_secs, ai_confidence, ai_categories) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                    guild_id, user_id, moderator_id, action, reason,
                    message_content, channel_id, duration_secs, ai_confidence, ai_categories or [],
                )
            elif self.db.is_sqlite():
                cats = ",".join(ai_categories) if ai_categories else None
                await self.db.execute(
                    None,
                    guild_id, user_id, moderator_id, action, reason,
                    message_content, channel_id, duration_secs, ai_confidence, cats,
                    sqlite_query=(
                        "INSERT INTO mod_actions (guild_id, user_id, moderator_id, action, reason, "
                        "message_content, channel_id, duration_secs, ai_confidence, ai_categories) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)"
                    ),
                )
        except Exception as e:
            logger.error(f"Failed to log mod action: {e}")

    async def increment_warn_count(self, guild_id: int, user_id: int) -> int:
        try:
            if self.db.is_postgres():
                row = await self.db.fetchrow(
                    "INSERT INTO user_punishments (guild_id, user_id, warn_count, last_action, last_at) "
                    "VALUES ($1, $2, 1, 'warn', NOW()) "
                    "ON CONFLICT (guild_id, user_id) "
                    "DO UPDATE SET warn_count = user_punishments.warn_count + 1, "
                    "last_action = 'warn', last_at = NOW() RETURNING warn_count",
                    guild_id, user_id,
                )
                return row["warn_count"] if row else 1
            elif self.db.is_sqlite():
                existing = await self.db.fetchrow(
                    None, guild_id, user_id,
                    sqlite_query="SELECT warn_count FROM user_punishments WHERE guild_id=? AND user_id=?",
                )
                if existing:
                    new_count = (existing["warn_count"] or 0) + 1
                    await self.db.execute(
                        None, new_count, guild_id, user_id,
                        sqlite_query="UPDATE user_punishments SET warn_count=? WHERE guild_id=? AND user_id=?",
                    )
                    return new_count
                else:
                    await self.db.execute(
                        None, guild_id, user_id,
                        sqlite_query="INSERT INTO user_punishments (guild_id, user_id, warn_count) VALUES (?,?,1)",
                    )
                    return 1
        except Exception as e:
            logger.error(f"Failed to increment warn count: {e}")
            return 0

    def track_spam(self, guild_id: int, user_id: int) -> int:
        import time
        now    = time.monotonic()
        window = self.settings.SPAM_THRESHOLD_SECONDS
        guild_map  = self._spam_tracker.setdefault(guild_id, {})
        timestamps = guild_map.setdefault(user_id, [])
        timestamps[:] = [t for t in timestamps if now - t < window]
        timestamps.append(now)
        return len(timestamps)

    @staticmethod
    async def _safe_respond(interaction: discord.Interaction, content: str, ephemeral: bool = True) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=ephemeral)
            else:
                await interaction.response.send_message(content, ephemeral=ephemeral)
        except Exception as e:
            logger.debug(f"Could not respond to interaction: {e}")

    # ─── Background tasks ─────────────────────────────────────────────────────

    @tasks.loop(minutes=10)
    async def _cache_cleanup_loop(self) -> None:
        try:
            removed = await self.license_manager.purge_expired_cache()
            if removed:
                logger.debug(f"Purged {removed} stale license cache entries")
        except Exception as e:
            logger.error(f"Cache cleanup error: {e}")

    @tasks.loop(hours=1)
    async def _license_expiry_check(self) -> None:
        try:
            if self.db.is_postgres():
                result = await self.db.execute(
                    "UPDATE licenses SET active = FALSE "
                    "WHERE active = TRUE AND expires_at IS NOT NULL AND expires_at < NOW()"
                )
                logger.debug(f"License expiry check complete: {result}")
            elif self.db.is_sqlite():
                await self.db.execute(
                    None,
                    sqlite_query=(
                        "UPDATE licenses SET active = 0 "
                        "WHERE active = 1 AND expires_at IS NOT NULL "
                        "AND expires_at < datetime('now')"
                    ),
                )
        except Exception as e:
            logger.error(f"License expiry check error: {e}")

    @_cache_cleanup_loop.before_loop
    async def _before_cache_cleanup(self) -> None:
        await self.wait_until_ready()

    @_license_expiry_check.before_loop
    async def _before_expiry_check(self) -> None:
        await self.wait_until_ready()
