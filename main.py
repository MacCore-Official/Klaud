import discord
from discord.ext import commands
from discord import app_commands
import os
import sys
import asyncio
import logging
import datetime
import time
import traceback
from typing import Optional, List, Dict, Any

# Internal System Logic
from database.connection import db
from core.license_manager import LicenseManager
from services.gemini_service import gemini_ai

# --- PROFESSIONAL LOGGING CONFIGURATION ---
class KlaudSystemLogger(logging.Formatter):
    def format(self, record):
        log_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"[{log_time}] [{record.levelname}] {record.name}: {record.getMessage()}"

logger = logging.getLogger("Klaud.Kernel")
logger.setLevel(logging.INFO)
sys_handler = logging.StreamHandler(sys.stdout)
sys_handler.setFormatter(KlaudSystemLogger())
logger.addHandler(sys_handler)

class KlaudNinja(commands.Bot):
    """
    KLAUD-NINJA ENTERPRISE OS v4.8.0
    The master controller for AI-integrated server management.
    """
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=discord.Intents.all(),
            help_command=None,
            owner_id=1269145029943758899,
            chunk_guilds_at_startup=True
        )
        self.start_time = time.time()
        self.version = "2026.PRO.FINAL"

    async def setup_hook(self) -> None:
        """Executed before the gateway connects."""
        logger.info("--- INITIATING SYSTEM BOOT SEQUENCE ---")
        
        # 1. Database Connectivity
        try:
            await db.connect()
            logger.info("✅ KERNEL: Database Connection [ONLINE]")
        except Exception as e:
            logger.critical(f"❌ KERNEL: Database Auth Failed: {e}")
            sys.exit(1)

        # 2. Extension Mounting
        await self._load_subsystems()

        # 3. Command Tree Sync
        try:
            logger.info("🔄 GATEWAY: Syncing Application Commands...")
            await self.tree.sync()
            logger.info("✅ GATEWAY: Commands Synchronized.")
        except Exception as e:
            logger.error(f"⚠️ GATEWAY: Sync Failure: {e}")

    async def _load_subsystems(self):
        """Iteratively loads all logic modules from the cogs directory."""
        if not os.path.exists('./cogs'):
            os.makedirs('./cogs')
            return

        for filename in os.listdir('./cogs'):
            if filename.endswith('.py') and not filename.startswith('__'):
                try:
                    await self.load_extension(f'cogs.{filename[:-3]}')
                    logger.info(f"✅ SUBSYSTEM: {filename} LOADED")
                except Exception:
                    logger.error(f"❌ SUBSYSTEM: {filename} FAILED:\n{traceback.format_exc()}")

    async def on_ready(self):
        """Final stabilization confirmation."""
        logger.info(f"--- KLAUD-NINJA ONLINE: {self.user} ---")
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="Licensed Servers | /license")
        )

    async def on_message(self, message: discord.Message):
        """The Central Traffic Controller."""
        if message.author.bot or not message.guild:
            return

        # 1. Mention/AI Interaction Handling
        if self.user.mentioned_in(message) and not message.mention_everyone:
            # Check License
            is_licensed = await LicenseManager.has_access(message.guild.id)
            if not is_licensed:
                return await message.reply("🔒 **License Required.** Use `/license_redeem` to unlock AI protocols.")

            # Trigger AI Command Logic (Parsed by Gemini)
            await self.process_ai_intent(message)

        await self.process_commands(message)

    async def process_ai_intent(self, message: discord.Message):
        """Handles administrative intent parsing via Gemini."""
        async with message.channel.typing():
            try:
                # Use a timeout to prevent the 'silent' hang
                intent = await asyncio.wait_for(
                    gemini_ai.parse_admin_intent(message.content), 
                    timeout=15.0
                )
                
                if intent.get('action') == 'create_channels':
                    # Logic for creating channels based on AI intent
                    await message.reply(f"🛠️ **Protocol Initiated:** Creating {intent.get('count', 1)} channels.")
                else:
                    await message.reply("🧠 **KLAUD AI:** I heard you, but no administrative action was identified in your request.")
            
            except asyncio.TimeoutError:
                await message.reply("⏳ **Neural Timeout:** Gemini took too long to respond. Please try again.")
            except Exception as e:
                logger.error(f"AI Intent Error: {e}")
                await message.reply("⚠️ **Neural Fault:** An error occurred while processing your request.")

# --- EXECUTION ---
if __name__ == "__main__":
    bot = KlaudNinja()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.critical("DISCORD_TOKEN MISSING")
        sys.exit(1)

    async def run():
        async with bot:
            await bot.start(token)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("System Offline.")
