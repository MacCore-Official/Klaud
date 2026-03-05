"""
KLAUD-NINJA — Main Entry Point
Bootstraps logging, validates settings, and launches the bot.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Ensure the bot package root is on sys.path regardless of how this is invoked
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import Settings
from core.bot import KlaudBot
from utils.smart_logger import setup_logging


async def main() -> None:
    settings = Settings()

    # Initialise logging first so every subsequent log is captured
    setup_logging(level=settings.LOG_LEVEL, log_file=settings.LOG_FILE)
    logger = logging.getLogger("klaud.main")

    logger.info("=" * 60)
    logger.info("  KLAUD-NINJA — AI Moderation & Automation Bot")
    logger.info("  License-Only SaaS | Starting up...")
    logger.info("=" * 60)

    try:
        settings.validate()
    except ValueError as exc:
        logger.critical(str(exc))
        sys.exit(1)

    bot = KlaudBot(settings=settings)

    try:
        async with bot:
            logger.info("Connecting to Discord gateway...")
            await bot.start(settings.DISCORD_TOKEN)
    except KeyboardInterrupt:
        logger.info("Shutdown requested by operator (KeyboardInterrupt)")
    except Exception as exc:
        msg = str(exc).lower()
        if "improper token" in msg or "401" in msg:
            logger.critical("Discord login failed — check that DISCORD_TOKEN is correct.")
        else:
            logger.critical(f"Fatal error: {exc}", exc_info=True)
        sys.exit(1)
    finally:
        if not bot.is_closed():
            await bot.close()
        logger.info("KLAUD-NINJA has shut down cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
