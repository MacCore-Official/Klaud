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
import platform
from typing import Optional, List, Dict, Any, Union

# Internal Infrastructure
from database.connection import db
from core.license_manager import LicenseManager
from services.gemini_service import gemini_ai

# --- HIGH-FIDELITY LOGGING ---
class KlaudSystemFormatter(logging.Formatter):
    def format(self, record):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"[{timestamp}] [{record.levelname}] {record.name}: {record.getMessage()}"

logger = logging.getLogger("Klaud.Kernel")
logger.setLevel(logging.INFO)
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(KlaudSystemFormatter())
logger.addHandler(stream_handler)

class KlaudNinja(commands.Bot):
    """
    KLAUD-NINJA ENTERPRISE OS v4.8.0
    Constructed for high-concurrency environments and AI orchestration.
    """
    def __init__(self):
        _intents = discord.Intents.all()
        _intents.members = True
        _intents.message_content = True
        
        super().__init__(
            command_prefix="!",
            intents=_intents,
            help_command=None,
            owner_id=1269145029943758899,
            chunk_guilds_at_startup=True
        )
        
        self.start_time = time.time()
        self.kernel_version = "2026.PRO.STABLE"
        self.processed_messages = 0

    async def setup_hook(self) -> None:
        """The Pre-Gateway Boot Sequence."""
        logger.info("-" * 60)
        logger.info(f"BOOTING KLAUD-NINJA OS ON {platform.system()}")
        logger.info("-" * 60)
        
        # 1. Database Connectivity
        try:
            await db.connect()
            logger.info("✅ KERNEL: Database Persistence [ONLINE]")
        except Exception as e:
            logger.critical(f"❌ KERNEL: Database Auth Failed: {e}")
            sys.exit(1)

        # 2. Subsystem Mounting (Cogs)
        await self._mount_subsystems()

        # 3. Command Synchronization
        try:
            logger.info("🔄 GATEWAY: Syncing Application Commands...")
            await self.tree.sync()
            logger.info("✅ GATEWAY: Commands Synchronized.")
        except Exception as e:
            logger.error(f"⚠️ GATEWAY: Tree Sync Failure: {e}")

    async def _mount_subsystems(self):
        """Iterative loading of modular components."""
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
        logger.info(f"--- KLAUD-NINJA AUTHORITY ESTABLISHED: {self.user} ---")
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="over Licensed Assets | /license")
        )

    async def on_message(self, message: discord.Message):
        """The Central Traffic Controller."""
        if message.author.bot or not message.guild:
            return

        self.processed_messages += 1

        # 1. Mention/AI Interaction Protocol
        if self.user.mentioned_in(message) and not message.mention_everyone:
            # CHECK LICENSE
            is_licensed = await LicenseManager.has_access(message.guild.id)
            if not is_licensed:
                embed = discord.Embed(
                    title="🛡️ KLAUD: UNAUTHORIZED INSTANCE",
                    description="This server lacks a valid Enterprise License. AI features are disabled.",
                    color=discord.Color.dark_red()
                )
                return await message.reply(embed=embed)

            # Trigger Intent Parsing
            await self.process_neural_intent(message)

        await self.process_commands(message)

    async def process_neural_intent(self, message: discord.Message):
        """Handles administrative intent parsing via Gemini with safe timeouts."""
        async with message.channel.typing():
            try:
                intent = await asyncio.wait_for(
                    gemini_ai.parse_admin_intent(message.content), 
                    timeout=15.0
                )
                
                if intent.get('action') == 'create_channels':
                    count = intent.get('count', 1)
                    name = intent.get('base_name', 'new-channel')
                    for i in range(count):
                        await message.guild.create_text_channel(f"{name}-{i+1}")
                    await message.reply(f"✅ **Protocol Success:** Created {count} channels.")
                else:
                    await message.reply("🧠 **KLAUD AI:** I am listening, but no admin action was detected.")
            
            except asyncio.TimeoutError:
                await message.reply("⏳ **Neural Timeout:** The AI failed to respond in time. Please retry.")
            except Exception as e:
                logger.error(f"Neural Intent Error: {e}")
                await message.reply("⚠️ **Neural Fault:** An internal error occurred.")

if __name__ == "__main__":
    bot = KlaudNinja()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.critical("DISCORD_TOKEN MISSING")
        sys.exit(1)

    async def start_instance():
        async with bot:
            await bot.start(token)

    try:
        asyncio.run(start_instance())
    except KeyboardInterrupt:
        logger.info("Manual Shutdown.")
