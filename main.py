import discord
from discord.ext import commands
import os
import asyncio
import logging
from database.connection import db

# Setup professional logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s:%(levelname)s:%(name)s: %(message)s'
)
logger = logging.getLogger("KlaudMain")

class KlaudBot(commands.Bot):
    def __init__(self):
        # Intents.all() is required to see pings and message content
        intents = discord.Intents.all()
        super().__init__(
            command_prefix="!", 
            intents=intents,
            help_command=None
        )

    async def setup_hook(self):
        """This runs before the bot connects to Discord."""
        logger.info("🛠️ Initializing Database...")
        await db.connect()

        # Load Cogs from the /cogs folder
        if os.path.exists('./cogs'):
            for filename in os.listdir('./cogs'):
                if filename.endswith('.py'):
                    try:
                        await self.load_extension(f'cogs.{filename[:-3]}')
                        logger.info(f"✅ Loaded extension: {filename}")
                    except Exception as e:
                        logger.error(f"❌ Failed to load {filename}: {e}")

        # Sync Slash Commands with Discord API
        try:
            logger.info("🔄 Syncing slash commands...")
            synced = await self.tree.sync()
            logger.info(f"✅ Successfully synced {len(synced)} commands.")
        except Exception as e:
            logger.error(f"❌ Slash sync failed: {e}")

    async def on_ready(self):
        logger.info(f"🚀 Bot is online and logged in as {self.user} (ID: {self.user.id})")
        await self.change_presence(activity=discord.Game(name="/license_activate"))

    async def on_message(self, message):
        # Ignore messages from the bot itself
        if message.author == self.user:
            return

        # FORCE REPLY TEST: If you ping the bot, it will reply regardless of license
        if self.user.mentioned_in(message):
            logger.info(f"📩 Pinged by {message.author} in {message.guild.name}")
            await message.channel.send(f"👋 **I am online!**\n\n- Database: `Connected`\n- License Status: `Checking...` \n\nIf my other commands aren't working, use `/license_generate` to get started!")

        # This line allows normal prefix commands (like !help) to work
        await self.process_commands(message)

# Initialize the bot
bot = KlaudBot()

async def start_bot():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.critical("❌ DISCORD_TOKEN is missing from environment variables!")
        return

    async with bot:
        try:
            await bot.start(token)
        except discord.LoginFailure:
            logger.critical("❌ Invalid Discord Token provided.")
        except Exception as e:
            logger.error(f"❌ Fatal error during startup: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(start_bot())
    except KeyboardInterrupt:
        logger.info("👋 Bot shutting down...")
