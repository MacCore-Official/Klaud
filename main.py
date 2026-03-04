import discord
from discord.ext import commands
from discord import app_commands
import os
import sys
import asyncio
import logging
import time
import datetime
from typing import Optional

# Advanced Persistence & Logic Imports w
from database.connection import db
from core.license_manager import LicenseManager
from services.gemini_service import gemini_ai

# --- PRODUCTION LOGGING CONFIGURATION ---
# We use a custom formatter to make Northflank logs readable and professional.
class KlaudFormatter(logging.Formatter):
    def format(self, record):
        log_fmt = "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
        formatter = logging.Formatter(log_fmt, datefmt="%Y-%m-%d %H:%M:%S")
        return formatter.format(record)

handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(KlaudFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger("Klaud.Main")

class KlaudBot(commands.Bot):
    """
    KLAUD-NINJA COMMAND CENTER (2026 Production Build).
    Responsible for Sharding, Cog Orchestration, and Global Authority enforcement.
    """
    def __init__(self):
        # Intents: Essential for AI analysis (Message Content) and Moderation (Members)
        intents = discord.Intents.all()
        
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            case_insensitive=True,
            owner_id=1269145029943758899, # Primary Authority ID
            application_id=1475302135905259682 # Bot App ID
        )
        
        # System Telemetry
        self.start_time = time.time()
        self.version = "4.2.0-PRO"
        self.maintenance_mode = False

    async def setup_hook(self):
        """
        The Master Initialization sequence.
        Executes before the bot connects to the Discord Gateway.
        """
        logger.info("-" * 50)
        logger.info(f"BOOTING KLAUD-NINJA OS v{self.version}")
        logger.info("-" * 50)

        # 1. Database Persistence Layer
        # Connects to the PostgreSQL pool and runs migrations.
        try:
            await db.connect()
            logger.info("✅ Database Pool: INITIALIZED")
        except Exception as e:
            logger.critical(f"❌ Database Pool: FAILED ({e})")
            sys.exit(1)

        # 2. Dynamic Subsystem (Cog) Loading
        # Scans directories for Moderation, AI, and Licensing modules.
        subsystems = ['cogs', 'core']
        for folder in subsystems:
            if os.path.exists(f'./{folder}'):
                for filename in os.listdir(f'./{folder}'):
                    if filename.endswith('.py') and not filename.startswith('__'):
                        ext_path = f"{folder}.{filename[:-3]}"
                        try:
                            # Avoid double-loading the license manager if it's imported elsewhere
                            if ext_path not in self.extensions:
                                await self.load_extension(ext_path)
                                logger.info(f"✅ Subsystem: {ext_path} LOADED")
                        except Exception as e:
                            logger.error(f"❌ Subsystem: {ext_path} FAILED ({e})")

        # 3. Global Command Tree Synchronization
        # Forces Discord to update Slash Commands across all servers.
        try:
            logger.info("🔄 Synchronizing Global Command Tree...")
            synced = await self.tree.sync()
            logger.info(f"✅ Sync Complete: {len(synced)} commands active.")
        except Exception as e:
            logger.error(f"⚠️ Tree Sync Error: {e}")

    async def on_ready(self):
        """
        Triggered when the bot has established a stable connection.
        """
        uptime_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info("-" * 50)
        logger.info(f"KLAUD-NINJA IS ONLINE AND FULLY AUTHORIZED")
        logger.info(f"Logged in as: {self.user.name}#{self.user.discriminator}")
        logger.info(f"Uptime Started: {uptime_str}")
        logger.info(f"Discord.py Version: {discord.__version__}")
        logger.info("-" * 50)

        # Rotating Rich Presence
        activity = discord.Activity(
            type=discord.ActivityType.watching, 
            name="over Licensed Servers | /license"
        )
        await self.change_presence(status=discord.Status.online, activity=activity)

    async def on_message(self, message: discord.Message):
        """
        The Primary Traffic Controller.
        Enforces Licensing Gates and AI Interaction Protocols.
        """
        # Ignore bots and system messages
        if message.author.bot or not message.guild:
            return

        # 1. Authority Check (Global Maintenance)
        if self.maintenance_mode and message.author.id != self.owner_id:
            return

        # 2. Mention Interaction (The "AI Brain" Trigger)
        if self.user.mentioned_in(message) and not message.mention_everyone:
            # CHECK LICENSE: Since there is no free tier, this is mandatory.
            is_licensed = await LicenseManager.has_access(message.guild.id)
            
            if not is_licensed:
                embed = discord.Embed(
                    title="🚫 SYSTEM LOCKED",
                    description=(
                        "KLAUD-NINJA is currently in **Dormant Mode** for this server.\n\n"
                        "**Reason:** No valid Enterprise License detected.\n"
                        "**Action:** Please use `/license_redeem` to activate."
                    ),
                    color=discord.Color.from_rgb(45, 45, 45) # Sleek Dark Grey
                )
                return await message.reply(embed=embed)

            # Process AI instruction (AI Logic resides in AdminAI cog)
            # This allows the command processor to pass the message to cogs.
            pass

        # Allow standard commands/cogs to process the message
        await self.process_commands(message)

    # --- ERROR HANDLING ---
    async def on_command_error(self, ctx, error):
        """Global Command Error Handler."""
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.NotOwner):
            return await ctx.send("❌ Authority Override Denied: Owner Only.", delete_after=5)
        logger.error(f"Command Error: {error}")

# --- GLOBAL SHUTDOWN HANDLER ---
async def shutdown(bot):
    """Ensures the database pool closes gracefully to prevent hung connections."""
    logger.info("Initiating Safe Shutdown...")
    if db.pool:
        await db.pool.close()
    await bot.close()
    logger.info("System Offline.")

# --- EXECUTION ---
async def main():
    bot = KlaudBot()
    token = os.getenv("DISCORD_TOKEN")
    
    if not token:
        logger.critical("FATAL: DISCORD_TOKEN is missing from the environment variables.")
        return

    async with bot:
        try:
            await bot.start(token)
        except KeyboardInterrupt:
            await shutdown(bot)
        except Exception as e:
            logger.critical(f"SYSTEM CRASH: {e}", exc_info=True)
            await shutdown(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
