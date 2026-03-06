"""
KLAUD-NINJA — Core Bot
Subclasses commands.Bot to attach shared services (Groq client)
and manage the startup sequence (DB, AI, cog loading, command sync).
"""

from __future__ import annotations

import logging
import os

import discord
from discord.ext import commands

from ai.groq_client import GroqClient

log = logging.getLogger("klaud.bot")

_EXTENSIONS = [
    "core.events",
    "cogs.moderation",
    "cogs.ai_commands",
    "cogs.config",
]


class KlaudBot(commands.Bot):
    """
    The main Klaud-Ninja bot instance.
    Access the Groq client via bot.groq anywhere a bot reference is available.
    """

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members         = True
        intents.guilds          = True
        intents.moderation      = True

        super().__init__(
            command_prefix=commands.when_mentioned,   # Prefix = @mention only for non-slash
            intents=intents,
            help_command=None,
            case_insensitive=True,
        )

        # Shared Groq client — attached before setup_hook
        self.groq = GroqClient(
            api_key=os.getenv("GROQ_API_KEY", ""),
            model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            timeout=20.0,
            max_retries=3,
        )

    # ── Startup ───────────────────────────────────────────────────────────────

    async def setup_hook(self) -> None:
        """Called once before the gateway connection is established."""
        log.info("Starting up Klaud-Ninja...")

        # Initialise Groq
        await self.groq.initialise()

        # Load cogs
        for ext in _EXTENSIONS:
            try:
                await self.load_extension(ext)
                log.info(f"  ✓ Loaded {ext}")
            except Exception as exc:
                log.error(f"  ✗ Failed to load {ext}: {exc}", exc_info=True)

        # Sync slash commands globally
        try:
            synced = await self.tree.sync()
            log.info(f"  ✓ Synced {len(synced)} slash command(s)")
        except Exception as exc:
            log.error(f"  ✗ Slash command sync failed: {exc}")

    # ── Shutdown ──────────────────────────────────────────────────────────────

    async def close(self) -> None:
        log.info("Klaud-Ninja shutting down...")
        await super().close()
