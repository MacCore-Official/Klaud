import discord
from discord.ext import commands
import os
import asyncio
import logging
from core.database import db  # Ensure this matches your file name in /core

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Klaud")

class KlaudBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(
            command_prefix="!", 
            intents=intents,
            help_command=None
        )

    async def setup_hook(self):
        # 1. Connect to Supabase
        await db.connect()

        # 2. Load Extensions (Cogs)
        # This looks into your /cogs folder and loads every file
        if os.path.exists('./cogs'):
            for filename in os.listdir('./cogs'):
                if filename.endswith('.py'):
                    try:
                        await self.load_extension(f'cogs.{filename[:-3]}')
                        logger.info(f"✅ Loaded extension: {filename}")
                    except Exception as e:
                        logger.error(f"❌ Failed to load {filename}: {e}")

        # 3. Sync Slash Commands
        await self.tree.sync()
        logger.info("✅ Slash commands synced globally.")

    async def on_ready(self):
        logger.info(f'🚀 Logged in as {self.user} (ID: {self.user.id})')
        await self.change_presence(activity=discord.Game(name="Managing Klaud"))

async def main():
    bot = KlaudBot()
    
    # Check for Token
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("❌ DISCORD_TOKEN is missing in Northflank variables!")
        return

    async with bot:
        await bot.start(token)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
