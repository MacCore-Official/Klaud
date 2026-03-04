import discord
from discord.ext import commands
import os
import sys
import asyncio
import logging
import signal
from typing import List

# Professional Logic Imports
from database.connection import db
from core.license_manager import LicenseManager
from services.gemini_service import gemini_ai

# --- STANDARDIZED LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger("Klaud.Main")

class KlaudNinja(commands.Bot):
    """
    KLAUD-NINJA MASTER AUTHORITY v4.5
    Constructed for 24/7 high-availability deployment.
    """
    def __init__(self):
        # Intents are mandatory for AI message reading
        intents = discord.Intents.all()
        
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            owner_id=1269145029943758899
        )
        self.system_version = "2026.1.PRO"

    async def setup_hook(self) -> None:
        """
        The critical boot sequence. 
        If any of this fails, the bot will intentionally crash (Exit 1) 
        so the container can restart.
        """
        logger.info(f"--- BOOTING KLAUD-NINJA OS v{self.system_version} ---")

        # 1. DATABASE CONNECTIVITY
        try:
            await db.connect()
            logger.info("✅ Persistence Layer: ONLINE")
        except Exception as e:
            logger.critical(f"❌ Persistence Layer: FAILED ({e})")
            sys.exit(1)

        # 2. COG SUBSYSTEM INITIALIZATION
        # We only load 'cogs' to avoid loading 'core' utilities as extensions
        cog_dir = './cogs'
        if os.path.exists(cog_dir):
            for filename in os.listdir(cog_dir):
                if filename.endswith('.py') and not filename.startswith('__'):
                    try:
                        await self.load_extension(f'cogs.{filename[:-3]}')
                        logger.info(f"✅ Subsystem: {filename} LOADED")
                    except Exception as e:
                        logger.error(f"❌ Subsystem: {filename} ERROR ({e})")

        # 3. GLOBAL SLASH COMMAND SYNC
        try:
            logger.info("🔄 Synchronizing Command Tree...")
            await self.tree.sync()
            logger.info("✅ Command Tree: SYNCED")
        except Exception as e:
            logger.error(f"⚠️ Command Tree Sync Warning: {e}")

    async def on_ready(self):
        logger.info("-" * 40)
        logger.info(f"KLAUD-NINJA AUTHORIZED: {self.user}")
        logger.info(f"GATEWAY LATENCY: {round(self.latency * 1000)}ms")
        logger.info("-" * 40)
        
        # Set Activity
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, 
                name="over Enterprise Assets"
            )
        )

    async def on_message(self, message: discord.Message):
        """
        The Hard-Gate License Check.
        No License = The Bot ignores all AI/Mod pings.
        """
        if message.author.bot or not message.guild:
            return

        # Check if the bot is mentioned (AI Command Trigger)
        if self.user.mentioned_in(message):
            # Strict License Logic
            is_licensed = await LicenseManager.has_access(message.guild.id)
            
            if not is_licensed:
                embed = discord.Embed(
                    title="🔒 SYSTEM DORMANT",
                    description="This server is not authorized. Please redeem a **KLAUD-Key**.",
                    color=discord.Color.dark_red()
                )
                return await message.reply(embed=embed)

        await self.process_commands(message)

# --- EXECUTION WRAPPER ---
# This is what keeps the container from exiting with Code 0.
async def run_system():
    token = os.getenv("DISCORD_TOKEN")
    
    if not token:
        logger.critical("FATAL: DISCORD_TOKEN is missing. System cannot start.")
        sys.exit(1)

    bot = KlaudNinja()
    
    # Use 'async with' for clean resource management
    async with bot:
        try:
            # This starts the bot and waits forever
            await bot.start(token)
        except KeyboardInterrupt:
            logger.info("Manual shutdown signal received.")
        except Exception as e:
            logger.critical(f"System encountered a fatal runtime error: {e}")
        finally:
            if not bot.is_closed():
                await bot.close()
            logger.info("System Cleanup Complete. Exiting.")

if __name__ == "__main__":
    try:
        # Standard Python 3.11+ way to run the async entry point
        asyncio.run(run_system())
    except KeyboardInterrupt:
        pass
