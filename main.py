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
from typing import Optional, List, Union, Dict, Any, Literal

# Internal Infrastructure
from database.connection import db
from core.license_manager import LicenseManager
from services.gemini_service import gemini_ai

# --- HIGH-FIDELITY LOGGING ENGINE ---
class KlaudSystemFormatter(logging.Formatter):
    """Custom formatter for enterprise-grade log readability."""
    grey = "\x1b[38;20m"
    blue = "\x1b[34;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    format_str = "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s (%(filename)s:%(lineno)d)"

    FORMATS = {
        logging.DEBUG: grey + format_str + reset,
        logging.INFO: blue + format_str + reset,
        logging.WARNING: yellow + format_str + reset,
        logging.ERROR: red + format_str + reset,
        logging.CRITICAL: bold_red + format_str + reset
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, datefmt="%Y-%m-%d %H:%M:%S")
        return formatter.format(record)

logger = logging.getLogger("Klaud.Kernel")
logger.setLevel(logging.INFO)
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(KlaudSystemFormatter())
logger.addHandler(stream_handler)

# --- THE KLAUD-NINJA AUTHORITY ---
class KlaudNinja(commands.Bot):
    """
    KLAUD-NINJA ENTERPRISE OS v4.6.0
    Constructed for high-concurrency environments and AI orchestration.
    """
    def __init__(self):
        # Intents Configuration: Total Visibility Required
        _intents = discord.Intents.all()
        _intents.members = True
        _intents.message_content = True
        _intents.presences = True
        
        super().__init__(
            command_prefix=self._determine_prefix,
            intents=_intents,
            help_command=None,
            owner_id=1269145029943758899,
            chunk_guilds_at_startup=True,
            heartbeat_timeout=180.0,
            max_messages=10000
        )
        
        # System Telemetry Data
        self.start_time = time.time()
        self.kernel_version = "2026.Q1.STABLE"
        self.deployment_id = f"KLAUD-{os.uname().nodename if hasattr(os, 'uname') else 'LOCAL'}"
        self.processed_messages = 0
        self.ai_tokens_consumed = 0

    @staticmethod
    async def _determine_prefix(bot, message: discord.Message) -> List[str]:
        """Dynamic prefix resolution logic."""
        return ["!", "k!", "ninja "]

    async def setup_hook(self) -> None:
        """
        The Pre-Gateway Boot Sequence.
        Verifies database integrity and initializes all neural cogs.
        """
        logger.info("-" * 60)
        logger.info(f"BOOTING KLAUD-NINJA OS ON {platform.system()} {platform.release()}")
        logger.info(f"PYTHON VERSION: {sys.version}")
        logger.info("-" * 60)
        
        # 1. Database Connectivity Auth
        try:
            await db.connect()
            logger.info("✅ KERNEL: Database Persistence Pool [ONLINE]")
        except Exception as error:
            logger.critical(f"❌ KERNEL: Database Auth Failed. System Termination. | Error: {error}")
            sys.exit(1)

        # 2. Subsystem Mounting (Cogs)
        await self._mount_subsystems()

        # 3. Command Synchronization
        try:
            logger.info("🔄 GATEWAY: Syncing Global Application Commands...")
            synced = await self.tree.sync()
            logger.info(f"✅ GATEWAY: {len(synced)} Command Definitions Propagated.")
        except Exception as e:
            logger.error(f"⚠️ GATEWAY: Tree Sync Failure: {e}")

    async def _mount_subsystems(self):
        """Iterative loading of modular components with error isolation."""
        subsystem_path = './cogs'
        if not os.path.exists(subsystem_path):
            os.makedirs(subsystem_path)
            return

        for filename in os.listdir(subsystem_path):
            if filename.endswith('.py') and not filename.startswith('__'):
                ext = f'cogs.{filename[:-3]}'
                try:
                    await self.load_extension(ext)
                    logger.info(f"✅ SUBSYSTEM: {ext} [MOUNTED]")
                except Exception as e:
                    logger.error(f"❌ SUBSYSTEM: {ext} [FAILED] | Traceback:\n{traceback.format_exc()}")

    # --- GLOBAL INTERCEPTORS ---
    async def on_ready(self):
        """Final execution state confirmation."""
        uptime_delta = round(time.time() - self.start_time, 2)
        logger.info("-" * 60)
        logger.info(f"SYSTEM STATUS: FULLY AUTHORIZED")
        logger.info(f"AUTHENTICATED AS: {self.user} (ID: {self.user.id})")
        logger.info(f"STABILIZATION TIME: {uptime_delta}s")
        logger.info("-" * 60)

        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="over Enterprise Assets | /license"
            )
        )

    async def on_message(self, message: discord.Message):
        """
        Primary Ingress Controller.
        Enforces Global Licensing Gates for all AI-enabled interactions.
        """
        if message.author.bot or not message.guild:
            return

        self.processed_messages += 1

        # 1. Mention/AI Interaction Protocol
        if self.user.mentioned_in(message) and not message.mention_everyone:
            # CHECK LICENSE: MANDATORY - NO FREE TIER ALLOWED
            is_licensed = await LicenseManager.has_access(message.guild.id)
            
            if not is_licensed:
                embed = discord.Embed(
                    title="🛡️ KLAUD-NINJA: UNAUTHORIZED INSTANCE",
                    description=(
                        "This server is running an **Unlicensed Version** of the KLAUD-NINJA OS.\n\n"
                        "All AI-driven moderation, administrative tasks, and executive commands "
                        "are locked behind a mandatory Enterprise Key."
                    ),
                    color=discord.Color.from_rgb(35, 35, 35),
                    timestamp=datetime.datetime.now()
                )
                embed.add_field(name="Deployment ID", value=f"`{self.deployment_id}`", inline=True)
                embed.add_field(name="Action", value="Use `/license_redeem`", inline=True)
                embed.set_footer(text="Contact System Administrator for Authorization")
                return await message.reply(embed=embed)

        # 2. Pipeline to Command Handlers
        await self.process_commands(message)

    async def on_error(self, event_method, *args, **kwargs):
        """Global exception capture for internal events."""
        logger.error(f"INTERNAL EVENT ERROR in {event_method}:\n{traceback.format_exc()}")

# --- SHUTDOWN ORCHESTRATION ---
async def terminate_system(bot: KlaudNinja):
    """Graceful deconstruction of system resources."""
    logger.info("--- INITIATING SYSTEM SHUTDOWN ---")
    if db.pool:
        await db.pool.close()
        logger.info("✅ DATABASE: Connection Pool Closed.")
    
    await bot.close()
    logger.info("✅ GATEWAY: Discord Connection Severed.")
    
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    [t.cancel() for t in tasks]
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("✅ KERNEL: All Tasks Cleaned. Process Terminating.")

def run_production():
    """Main process execution loop."""
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.critical("FATAL: DISCORD_TOKEN is not defined in the environment variables.")
        sys.exit(1)

    bot = KlaudNinja()

    async def entry_point():
        async with bot:
            try:
                await bot.start(token)
            except Exception:
                logger.error(f"CRITICAL KERNEL ERROR:\n{traceback.format_exc()}")

    # Loop Management for 24/7 Stability
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(entry_point())
    except KeyboardInterrupt:
        logger.info("SIGINT: Manual Override Detected.")
        loop.run_until_complete(terminate_system(bot))
    finally:
        loop.close()

if __name__ == "__main__":
    run_production()
