import asyncio
import os
from dotenv import load_dotenv
from core.bot import KlaudBot

load_dotenv()

async def main():
    bot = KlaudBot()
    await bot.start(os.getenv("DISCORD_TOKEN"))

if __name__ == "__main__":
    asyncio.run(main())
