import discord
from discord.ext import commands
import os
import asyncio
from database.connection import db

class KlaudBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all())

    async def setup_hook(self):
        # Initialize database first
        await db.connect()
        
        # Load Cogs
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py'):
                await self.load_extension(f'cogs.{filename[:-3]}')
        
        await self.tree.sync()
        print("✅ Bot is ready and commands are synced.")

bot = KlaudBot()

async def start_bot():
    async with bot:
        await bot.start(os.getenv("DISCORD_TOKEN"))

if __name__ == "__main__":
    asyncio.run(start_bot())
