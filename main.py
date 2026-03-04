import discord
from discord.ext import commands
import os
import asyncio
import logging
from database.connection import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Klaud")

class KlaudBot(commands.Bot):
    def __init__(self):
        # We use all intents so it can see pings and messages
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        logger.info("Connecting to Database...")
        await db.connect()

        # Loading cogs
        if os.path.exists('./cogs'):
            for filename in os.listdir('./cogs'):
                if filename.endswith('.py'):
                    try:
                        await self.load_extension(f'cogs.{filename[:-3]}')
                        logger.info(f"✅ Loaded {filename}")
                    except Exception as e:
                        logger.error(f"❌ Cog Error {filename}: {e}")

        await self.tree.sync()
        logger.info("✅ Slash commands synced.")

    async def on_ready(self):
        logger.info(f"🚀 Bot is LIVE as {self.user}")

    async def on_message(self, message):
        # DEBUG: This will show in Northflank whenever someone types/pings
        if self.user.mentioned_in(message):
            logger.info(f"📩 I was pinged by {message.author} in {message.guild}!")
        
        await self.process_commands(message)

bot = KlaudBot()

async def main():
    async with bot:
        await bot.start(os.getenv("DISCORD_TOKEN"))

if __name__ == "__main__":
    asyncio.run(main())
