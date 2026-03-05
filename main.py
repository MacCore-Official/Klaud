"""
KLAUD-NINJA — Entry Point
═══════════════════════════════════════════════════════════════════════════════
Bootstraps logging, validates configuration, and launches the bot.
This is the only file that should be executed directly.

Usage:
  python main.py

Environment variables are loaded from .env if present.
All configuration is documented in .env.example.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# ── Ensure project root is on the Python path ──────────────────────────────
# This allows all internal imports like `from config.settings import Settings`
# to work regardless of how/where the script is invoked.
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import Settings
from core.bot import KlaudBot
from utils.smart_logger import setup_logging


async def main() -> None:
    """Async entry point. Initialise everything and run the bot."""

    # ── 1. Load and validate configuration ────────────────────────────────────
    settings = Settings()

    # Set up logging first so all subsequent messages are properly formatted
    setup_logging(level=settings.LOG_LEVEL, log_file=settings.LOG_FILE)
    logger = logging.getLogger("klaud.main")

    logger.info("=" * 60)
    logger.info("  KLAUD-NINJA — AI Moderation & Automation Bot")
    logger.info("  Powered by Groq AI (llama-3.3-70b-versatile)")
    logger.info("  License-Only SaaS | Starting up...")
    logger.info("=" * 60)

    try:
        settings.validate()
    except ValueError as exc:
        logger.critical(f"Configuration error:\n{exc}")
        sys.exit(1)

    # ── 2. Instantiate and run the bot ────────────────────────────────────────
    bot = KlaudBot(settings=settings)

    try:
        async with bot:
            logger.info("Connecting to Discord gateway...")
            await bot.start(settings.DISCORD_TOKEN)

    except KeyboardInterrupt:
        logger.info("Shutdown requested (KeyboardInterrupt)")

    except Exception as exc:
        error_str = str(exc).lower()
        if "improper token" in error_str or "401" in error_str:
            logger.critical(
                "Discord authentication failed.\n"
                "Please check that DISCORD_TOKEN is correct in your .env file."
            )
        elif "privileged intent" in error_str:
            logger.critical(
                "Missing privileged intents.\n"
                "Enable 'Message Content Intent' and 'Server Members Intent' "
                "at https://discord.com/developers/applications"
            )
        else:
            logger.critical(f"Unexpected fatal error: {exc}", exc_info=True)
        sys.exit(1)

    finally:
        if not bot.is_closed():
            logger.info("Closing bot connection...")
            await bot.close()
        logger.info("KLAUD-NINJA has shut down cleanly. Goodbye.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
