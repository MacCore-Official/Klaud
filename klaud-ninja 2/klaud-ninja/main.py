"""
KLAUD-NINJA — Entry Point
Run with: python main.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from utils.logger import setup_logging

setup_logging(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("klaud.main")


async def main() -> None:
    token = os.getenv("DISCORD_TOKEN", "")
    if not token:
        log.critical("DISCORD_TOKEN is not set in .env — cannot start.")
        sys.exit(1)

    from core.bot import KlaudBot
    bot = KlaudBot()

    try:
        async with bot:
            await bot.start(token)
    except KeyboardInterrupt:
        log.info("Received keyboard interrupt — shutting down.")
    except Exception as exc:
        err = str(exc).lower()
        if "improper token" in err or "401" in err:
            log.critical("Invalid DISCORD_TOKEN. Check your .env file.")
        elif "privileged intent" in err:
            log.critical(
                "Missing privileged intents. Enable 'Message Content Intent' "
                "and 'Server Members Intent' in the Discord Developer Portal."
            )
        else:
            log.critical(f"Fatal error: {exc}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
