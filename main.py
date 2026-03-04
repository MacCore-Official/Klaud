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
import platform
import io
from typing import Optional, List, Union, Dict, Any, Literal

# Internal Authority Imports
from database.connection import db
from core.license_manager import LicenseManager
from services.gemini_service import gemini_ai

# --- PRODUCTION LOGGING CONFIGURATION ---
class KlaudFormatter(logging.Formatter):
    """Custom logs for Northflank/Production readability."""
    def format(self, record):
        log_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        level = record.levelname
        msg = record.getMessage()
        return f"[{log_time}] [{level}] {record.name}: {msg}"

logger = logging.getLogger("Klaud.Kernel")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(KlaudFormatter())
logger.addHandler(handler)

# --- THE MASTER BOT CLASS ---
class KlaudNinja(commands.Bot):
    """
    KLAUD-NINJA ENTERPRISE OS v4.7.0
    A high-availability AI orchestrator built for the 2026 stable environment.
    """
    def __init__(self):
        # Intents: Absolute Visibility
        intents = discord.Intents.all()
        intents.members = True
        intents.message_content = True
        
        super().__init__(
            command_prefix=self.get_custom_prefix,
            intents=intents,
            help_command=None,
            owner_id=1269145029943758899,
            chunk_guilds_at_startup=True,
            proxy=None
        )
        
        # Internal Telemetry
        self.boot_time = time.time()
        self.version = "2026.PRO.STABLE"
        self.total_packets_processed = 0
        self.maintenance_lock = False

    async def get_custom_prefix(self, bot, message: discord.Message) -> List[str]:
        """Dynamic prefix resolution."""
        return ["!", "k!", "K!"]

    async def setup_hook(self) -> None:
        """The Master Initialization Sequence."""
        logger.info("="*60)
        logger.info(f"KLAUD-NINJA KERNEL BOOT: {platform.system()} {platform.machine()}")
        logger.info(f"CORE VERSION: {self.version}")
        logger.info("="*60)

        # 1. DATABASE AUTHENTICATION
        try:
            await db.connect()
            logger.info("✅ KERNEL: Database Connection Established.")
        except Exception as e:
            logger.critical(f"❌ KERNEL: Database Fatal Error: {e}")
            sys.exit(1)

        # 2. EXTENSION MOUNTING
        await self._load_all_extensions()

        # 3. SLASH COMMAND SYNCHRONIZATION
        try:
            logger.info("🔄 GATEWAY: Syncing Application Commands...")
            synced = await self.tree.sync()
            logger.info(f"✅ GATEWAY: {len(synced)} Global Commands Verified.")
        except Exception as e:
            logger.error(f"⚠️ GATEWAY: Sync Failure: {e}")

    async def _load_all_extensions(self):
        """Iteratively mounts all Python modules in the cogs directory."""
        cog_path = './cogs'
        if not os.path.exists(cog_path):
            os.makedirs(cog_path)
            return

        for filename in os.listdir(cog_path):
            if filename.endswith('.py') and not filename.startswith('__'):
                ext = f'cogs.{filename[:-3]}'
                try:
                    await self.load_extension(ext)
                    logger.info(f"✅ SUBSYSTEM: {ext} Loaded.")
                except Exception as e:
                    logger.error(f"❌ SUBSYSTEM: {ext} Failed: {traceback.format_exc()}")

    # --- GLOBAL EVENT HANDLERS ---
    async def on_ready(self):
        """Final system stabilization check."""
        elapsed = round(time.time() - self.boot_time, 2)
        logger.info("="*60)
        logger.info(f"SYSTEM STATUS: ONLINE AND AUTHORIZED")
        logger.info(f"LOGGED IN AS: {self.user} ({self.user.id})")
        logger.info(f"STABILIZATION TIME: {elapsed}s")
        logger.info("="*60)

        activity = discord.Activity(
            type=discord.ActivityType.watching,
            name="over Enterprise Assets | /license"
        )
        await self.change_presence(status=discord.Status.online, activity=activity)

    async def on_message(self, message: discord.Message):
        """Primary Ingress Traffic Controller."""
        if message.author.bot or not message.guild:
            return

        self.total_packets_processed += 1

        # MENTION PROTOCOL: The Gateway to AI Features
        if self.user.mentioned_in(message) and not message.mention_everyone:
            # LICENSE GATE: NO FREE TIER
            is_licensed = await LicenseManager.has_access(message.guild.id)
            
            if not is_licensed:
                embed = discord.Embed(
                    title="🛡️ KLAUD: UNAUTHORIZED INSTANCE",
                    description=(
                        "**Access Denied.** This server lacks a valid Enterprise License.\n\n"
                        "To activate AI protocols and executive commands, "
                        "please redeem an authorized license key."
                    ),
                    color=discord.Color.dark_red(),
                    timestamp=datetime.datetime.now()
                )
                embed.set_footer(text="Enterprise Infrastructure Protection")
                return await message.reply(embed=embed)

        # Proceed to command logic
        await self.process_commands(message)

    async def on_error(self, event, *args, **kwargs):
        """Global internal exception interceptor."""
        logger.error(f"INTERNAL SYSTEM ERROR in {event}:\n{traceback.format_exc()}")

# --- SHUTDOWN LOGIC ---
async def shutdown(bot: KlaudNinja):
    """Gracefully terminates all active connections."""
    logger.info("Initiating Shutdown Sequence...")
    if db.pool:
        await db.pool.close()
        logger.info("Database closed.")
    await bot.close()
    logger.info("Gateway closed. System Offline.")

# --- ENTRY POINT ---
def run_klaud():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.critical("DISCORD_TOKEN MISSING.")
        sys.exit(1)

    bot = KlaudNinja()

    async def start():
        async with bot:
            try:
                await bot.start(token)
            except Exception:
                logger.error(f"FATAL BOOT ERROR:\n{traceback.format_exc()}")

    # Managed Event Loop
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(start())
    except KeyboardInterrupt:
        loop.run_until_complete(shutdown(bot))
    finally:
        loop.close()

if __name__ == "__main__":
    run_klaud()
