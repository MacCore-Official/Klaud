import discord
from discord.ext import commands
import os
import asyncio
import logging
from database.connection import db

# Configuration for Production-Grade Logging
# Ensures all events from Database to Cogs are captured in Northflank logs.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("Klaud.Main")

class KlaudBot(commands.Bot):
    """
    KLAUD-NINJA: Advanced AI Moderation & Automation Platform.
    Architecture designed for high availability and tiered server authorization.
    """
    def __init__(self):
        # Intents.all() is required for:
        # 1. Message Content (AI Analysis)
        # 2. Server Members (Verification/Auto-role)
        # 3. Presence/Voice (Behavior scoring)
        intents = discord.Intents.all()
        super().__init__(
            command_prefix="!", 
            intents=intents,
            help_command=None,
            case_insensitive=True
        )
        # Permanent Authority Reference
        self.owner_id_env = 1269145029943758899

    async def setup_hook(self):
        """
        Pre-initialization lifecycle hook.
        Establishes database connections and loads feature modules.
        """
        logger.info("Initializing KLAUD-NINJA Production Environment...")
        
        # 1. Establish Database Pool
        await db.connect()

        # 2. Dynamic Cog Loading
        # Scans the /cogs directory to initialize features like Moderation, AI, and Licensing.
        if os.path.exists('./cogs'):
            for filename in os.listdir('./cogs'):
                if filename.endswith('.py'):
                    try:
                        await self.load_extension(f'cogs.{filename[:-3]}')
                        logger.info(f"✅ Subsystem Loaded: {filename}")
                    except Exception as e:
                        # Non-fatal error logging: allows other cogs to continue loading
                        logger.error(f"❌ Subsystem Failure: {filename} -> {e}")
        else:
            logger.critical("FATAL: /cogs directory is missing. Core functionality unavailable.")

        # 3. Application Command Synchronization
        # Synchronizes Slash commands globally with the Discord API.
        try:
            logger.info("🔄 Synchronizing Command Tree with Discord Gateway...")
            synced = await self.tree.sync()
            logger.info(f"✅ Synchronization Successful. {len(synced)} Global Commands Registered.")
        except Exception as e:
            logger.error(f"⚠️ Command Tree Sync Error: {e}")

    async def on_ready(self):
        """
        Ready event triggered once the bot is connected and cached.
        """
        logger.info("-" * 40)
        logger.info(f"🚀 KLAUD-NINJA IS DEPLOYED AND ONLINE")
        logger.info(f"Username: {self.user} (ID: {self.user.id})")
        logger.info(f"Latency: {round(self.latency * 1000)}ms")
        logger.info("-" * 40)
        
        # Professional status display
        activity = discord.Activity(
            type=discord.ActivityType.watching, 
            name="over servers | /license"
        )
        await self.change_presence(status=discord.Status.online, activity=activity)

    async def on_message(self, message: discord.Message):
        """
        Universal message handler for pings, prefix-commands, and AI triggers.
        """
        if message.author.bot:
            return

        # Connectivity health-check: Bot responds when pinged without content
        if self.user.mentioned_in(message) and len(message.content.split()) == 1:
            logger.info(f"Ping received from {message.author} in {message.guild.name}")
            
            embed = discord.Embed(
                title="KLAUD-NINJA Status",
                color=discord.Color.blue(),
                description="Advanced AI Moderation & Automation"
            )
            embed.add_field(name="Infrastructure", value="`Northflank`", inline=True)
            embed.add_field(name="Database", value="`PostgreSQL Online`", inline=True)
            embed.add_field(name="Latency", value=f"`{round(self.latency * 1000)}ms`", inline=True)
            embed.set_footer(text="Use /license_status to check server activation.")
            
            await message.reply(embed=embed)

        # Allow commands.Cog functionality and !prefix commands
        await self.process_commands(message)

# System Execution
async def main():
    bot = KlaudBot()
    token = os.getenv("DISCORD_TOKEN")
    
    if not token:
        logger.critical("CORE ERROR: DISCORD_TOKEN not found in environment.")
        return

    async with bot:
        try:
            await bot.start(token)
        except Exception as e:
            logger.critical(f"UNHANDLED BOOT EXCEPTION: {e}", exc_info=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("KLAUD-NINJA shutdown by operator.")
