import discord
from discord.ext import commands
from discord import app_commands
import os
import sys
import asyncio
import logging
import datetime
import time
import signal
import traceback
from typing import Optional, List, Union, Dict, Any

# Internal System Logic
from database.connection import db
from core.license_manager import LicenseManager
from services.gemini_service import gemini_ai

# --- ADVANCED SYSTEM LOGGING ---
class KlaudConsoleHandler(logging.StreamHandler):
    """Custom formatter for professional 2026 container logs."""
    def emit(self, record):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        level = record.levelname
        name = record.name
        message = record.getMessage()
        sys.stdout.write(f"[{timestamp}] [{level}] [{name}]: {message}\n")
        sys.stdout.flush()

logger = logging.getLogger("Klaud.Core")
logger.setLevel(logging.INFO)
logger.addHandler(KlaudConsoleHandler())

# --- THE MASTER CLASS ---
class KlaudNinja(commands.Bot):
    """
    KLAUD-NINJA ENTERPRISE OS v4.5.0-PRO
    A high-availability, AI-integrated management system.
    """
    def __init__(self):
        # Operational Intents
        _intents = discord.Intents.all()
        _intents.members = True
        _intents.message_content = True
        
        super().__init__(
            command_prefix=self._get_dynamic_prefix,
            intents=_intents,
            help_command=None,
            owner_id=1269145029943758899,
            chunk_guilds_at_startup=True,
            heartbeat_timeout=150.0
        )
        
        # System Telemetry
        self.start_time = time.time()
        self.total_commands_executed = 0
        self.license_cache: Dict[int, bool] = {}
        self.version = "2026.Q1.REDACTED"

    @staticmethod
    async def _get_dynamic_prefix(bot, message: discord.Message) -> List[str]:
        """Allows for future custom prefix implementation per guild."""
        return ["!", "k!"]

    async def setup_hook(self) -> None:
        """
        Critical Boot Sequence:
        Pre-connection database verification and subsystem mounting.
        """
        logger.info("--- STARTING KLAUD-NINJA SYSTEM BOOT ---")
        
        # 1. Persistence Layer Authority
        try:
            await db.connect()
            logger.info("✅ DATABASE: Connection Pool Established.")
        except Exception as e:
            logger.critical(f"❌ DATABASE: Failed to establish authority. {e}")
            sys.exit(1)

        # 2. Extension Orchestration
        # This scans the 'cogs' directory for production modules.
        await self._load_extensions()

        # 3. Command Tree Synchronization
        # Ensures Slash commands are global and up-to-date.
        try:
            logger.info("🔄 API: Synchronizing Global Command Tree...")
            synced = await self.tree.sync()
            logger.info(f"✅ API: {len(synced)} Global Commands Synchronized.")
        except discord.HTTPException as e:
            logger.error(f"⚠️ API: Command Tree Sync Failed: {e}")

    async def _load_extensions(self):
        """Recursive loader for the bot's modular subsystems."""
        subsystems_dir = './cogs'
        if not os.path.exists(subsystems_dir):
            logger.warning("⚠️ SYSTEM: Cogs directory missing. Creating...")
            os.makedirs(subsystems_dir)
            return

        for filename in os.listdir(subsystems_dir):
            if filename.endswith('.py') and not filename.startswith('__'):
                ext = f'cogs.{filename[:-3]}'
                try:
                    await self.load_extension(ext)
                    logger.info(f"✅ SUBSYSTEM: Loaded {ext}")
                except Exception as e:
                    logger.error(f"❌ SUBSYSTEM: Failed {ext} | Error: {traceback.format_exc()}")

    # --- GLOBAL EVENT LISTENERS ---
    async def on_ready(self):
        """Confirmation of Gateway connectivity."""
        elapsed = round(time.time() - self.start_time, 2)
        logger.info("-" * 45)
        logger.info(f"KLAUD-NINJA IS ONLINE AND FULLY AUTHORIZED")
        logger.info(f"IDENTITY: {self.user} ({self.user.id})")
        logger.info(f"BOOT TIME: {elapsed}s | VERSION: {self.version}")
        logger.info("-" * 45)

        # Presence Protocol
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="over Licensed Assets | /license"
            )
        )

    async def on_message(self, message: discord.Message):
        """
        Primary Ingress Controller.
        Enforces License Gates and AI Mention Protocols.
        """
        if message.author.bot or not message.guild:
            return

        # 1. AI Mention Handler
        if self.user.mentioned_in(message) and not message.mention_everyone:
            # CHECK LICENSE: Since there is no free tier, this is mandatory.
            is_licensed = await LicenseManager.has_access(message.guild.id)
            
            if not is_licensed:
                embed = discord.Embed(
                    title="🛡️ KLAUD-NINJA: SYSTEM DORMANT",
                    description=(
                        "This instance is currently **Unlicensed**.\n\n"
                        "To activate AI-driven moderation and executive admin tools, "
                        "please provide a valid License Key."
                    ),
                    color=discord.Color.from_rgb(30, 30, 30)
                )
                embed.add_field(name="Action Required", value="Use `/license_redeem` to activate.")
                embed.set_footer(text="Enterprise Authority Management")
                return await message.reply(embed=embed)

        # 2. Command Pipeline
        await self.process_commands(message)

    async def on_command_completion(self, ctx):
        self.total_commands_executed += 1
        logger.info(f"EXEC: Command '{ctx.command}' used by {ctx.author}")

    async def on_error(self, event, *args, **kwargs):
        """Handles internal Discord event errors."""
        logger.error(f"INTERNAL EVENT ERROR: {event}\n{traceback.format_exc()}")

# --- SIGNAL & SHUTDOWN HANDLING ---
async def shutdown_sequence(bot: KlaudNinja):
    """Graceful termination of system resources."""
    logger.info("Initiating Graceful Shutdown Sequence...")
    
    # Close Database Pool
    if db.pool:
        await db.pool.close()
        logger.info("DATABASE: Connection pool closed.")

    # Close Bot Connection
    await bot.close()
    logger.info("GATEWAY: Discord connection terminated.")
    
    # Final cleanup
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    [t.cancel() for t in tasks]
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("SYSTEM: All tasks cancelled. Process exiting.")

# --- PRODUCTION RUNNER ---
def run_klaud():
    """Entry point for the Python process."""
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.critical("FATAL: DISCORD_TOKEN environment variable is missing.")
        sys.exit(1)

    bot = KlaudNinja()

    async def start():
        async with bot:
            try:
                await bot.start(token)
            except Exception:
                logger.error(f"CRITICAL RUNTIME ERROR:\n{traceback.format_exc()}")

    # Setup OS Signal Handling (SIGINT/SIGTERM)
    loop = asyncio.get_event_loop()
    
    try:
        loop.run_until_complete(start())
    except KeyboardInterrupt:
        logger.info("Manual Interrupt Detected.")
        loop.run_until_complete(shutdown_sequence(bot))
    finally:
        loop.close()

if __name__ == "__main__":
    run_klaud()
