"""
bot.py — Main entry point for Klaud Bot.

Startup sequence:
  1. Validate environment variables.
  2. Initialise the Discord client with required intents.
  3. Load all Cog extensions.
  4. Sync slash commands (guild-only during dev; global in production).
  5. Connect to Discord.
"""

from __future__ import annotations

import asyncio
import logging
import sys

import discord
from discord.ext import commands

import config
from config import validate_config

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("klaud")

# ── Extensions to load (order matters for dependencies) ───────────────────────
EXTENSIONS: list[str] = [
    "commands.license",       # License slash commands + periodic check
    "commands.ai_prompt",     # Custom AI rule management
    "commands.server_builder",# AI server builder
    "moderation.warnings",    # Warning slash commands
    "moderation.automod",     # Message listener (depends on warnings helper)
]


class KlaudBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True   # Required for reading messages in automod
        intents.members = True           # Required for member events and moderation
        intents.guilds = True

        super().__init__(
            command_prefix=config.BOT_PREFIX,
            intents=intents,
            description="Klaud — AI-powered Discord server manager",
            help_command=None,
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def setup_hook(self) -> None:
        """Called once before the bot connects; load extensions and sync commands."""
        for ext in EXTENSIONS:
            try:
                await self.load_extension(ext)
                log.info("Loaded extension: %s", ext)
            except Exception as exc:
                log.error("Failed to load extension %s: %s", ext, exc)

        # Sync global slash commands.
        # During development, replace with: await self.tree.sync(guild=discord.Object(id=YOUR_GUILD_ID))
        synced = await self.tree.sync()
        log.info("Synced %d slash command(s) globally.", len(synced))

    async def on_ready(self) -> None:
        log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log.info("  Klaud is online!")
        log.info("  Logged in as: %s (ID: %s)", self.user, self.user.id)
        log.info("  Serving %d guild(s)", len(self.guilds))
        log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="your server 👁️",
            )
        )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("Joined guild: %s (ID: %s)", guild.name, guild.id)
        # Try to DM the owner with onboarding instructions
        try:
            owner = await guild.fetch_member(guild.owner_id)
            embed = discord.Embed(
                title="👋 Thanks for adding Klaud!",
                description=(
                    "To get started you need to activate your license.\n\n"
                    "**Step 1:** Use `/activate-license key:<your_key>` in your server.\n"
                    "**Step 2:** (Optional) Add custom rules with `/ai-prompt add`.\n"
                    "**Step 3:** Build your server with `/build server <description>`.\n\n"
                    "Need help? Join our support server or check the documentation."
                ),
                color=discord.Color.blurple(),
            )
            await owner.send(embed=embed)
        except Exception:
            pass  # Owner may have DMs disabled

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        # Suppress expected errors for prefix commands (we mainly use slash commands)
        if isinstance(error, commands.CommandNotFound):
            return
        log.error("Command error: %s", error)


# ── Entrypoint ─────────────────────────────────────────────────────────────────

async def main() -> None:
    missing = validate_config()
    if missing:
        log.critical(
            "Missing required environment variables: %s\n"
            "Please create a .env file. See README.md for instructions.",
            ", ".join(missing),
        )
        sys.exit(1)

    bot = KlaudBot()
    async with bot:
        await bot.start(config.DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Klaud shutting down. Goodbye!")
