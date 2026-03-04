import discord
from discord.ext import commands
import os
import asyncio
import logging
from database.connection import db

# Production logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("Klaud.Main")

class KlaudBot(commands.Bot):
    """
    KLAUD-NINJA: High-performance AI Moderation and Automation Bot.
    Built for long-term stability and professional server management.
    """
    def __init__(self):
        # Intents.all() is mandatory for behavior tracking, AI analysis, and verification
        intents = discord.Intents.all()
        super().__init__(
            command_prefix="!", 
            intents=intents,
            help_command=None,
            case_insensitive=True
        )
        self.owner_id_env = 1269145029943758899 # KLAUD Authority

    async def setup_hook(self):
        """
        Asynchronous initialization sequence.
        1. Database connectivity
        2. Cog extension loading
        3. Global Slash command synchronization
        """
        logger.info("⚙️ Commencing KLAUD boot sequence...")
        
        # Phase 1: Persistence Layer
        await db.connect()

        # Phase 2: Feature Modularization (Cogs)
        # Architecture strictly follows the /cogs directory structure
        if os.path.exists('./cogs'):
            for filename in os.listdir('./cogs'):
                if filename.endswith('.py'):
                    try:
                        # Additive loading - preserving all existing modules
                        await self.load_extension(f'cogs.{filename[:-3]}')
                        logger.info(f"✅ Module Loaded: {filename}")
                    except Exception as e:
                        logger.error(f"❌ Module Failure: {filename} -> {e}", exc_info=True)
        else:
            logger.warning("⚠️ Critical directory /cogs not found.")

        # Phase 3: Global Command Synchronization
        try:
            logger.info("🔄 Synchronizing Application Command Tree...")
            synced = await self.tree.sync()
            logger.info(f"✅ Sync complete. {len(synced)} Global Commands active.")
        except Exception as e:
            logger.error(f"❌ Slash Tree Sync Failed: {e}")

    async def on_ready(self):
        """Finalization event after Discord Gateway connection."""
        logger.info("-" * 30)
        logger.info(f"🚀 KLAUD-NINJA IS ONLINE")
        logger.info(f"Bot Identity: {self.user} ({self.user.id})")
        logger.info(f"Discord.py: {discord.__version__}")
        logger.info("-" * 30)
        
        # Professional presence indicating the activation requirement
        activity = discord.Activity(
            type=discord.ActivityType.watching, 
            name="/license activate"
        )
        await self.change_presence(status=discord.Status.online, activity=activity)

    async def on_message(self, message: discord.Message):
        """
        Global message processor.
        Handles pings, prefix commands, and hands off to AI moderation cogs.
        """
        if message.author.bot:
            return

        # Connectivity Verification: Responds to pings to verify Bot/Gateway health
        if self.user.mentioned_in(message) and len(message.content.split()) == 1:
            logger.info(f"Health-check ping received from {message.author} in {message.guild}")
            
            # Diagnostic response showing system status
            status_embed = discord.Embed(
                title="KLAUD-NINJA System Status",
                color=discord.Color.blue(),
                description="AI Moderation & Automation System"
            )
            status_embed.add_field(name="Database", value="`ONLINE`", inline=True)
            status_embed.add_field(name="Gateway", value=f"`{round(self.latency * 1000)}ms`", inline=True)
            status_embed.set_footer(text="Production Build v2.4.0 | US-Central-1")
            
            await message.reply(embed=status_embed)

        # Standard command processing for prefix-based administrative tasks
        await self.process_commands(message)

# Bootstrapper
async def run_klaud():
    bot = KlaudBot()
    token = os.getenv("DISCORD_TOKEN")
    
    if not token:
        logger.critical("APPLICATION ABORTED: DISCORD_TOKEN environment variable is not set.")
        return

    async with bot:
        try:
            await bot.start(token)
        except discord.LoginFailure:
            logger.critical("APPLICATION ABORTED: Invalid Discord Token provided.")
        except Exception as e:
            logger.critical(f"UNHANDLED SYSTEM EXCEPTION: {e}", exc_info=True)

if __name__ == "__main__":
    try:
        asyncio.run(run_klaud())
    except KeyboardInterrupt:
        logger.info("System shutdown initiated by KLAUD-Ninja Operator.")
