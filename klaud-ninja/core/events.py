"""
KLAUD-NINJA — Core Events
Global Discord event handlers attached directly to the bot.
Covers guild join/leave, errors, and startup logging.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from database import queries

log = logging.getLogger("klaud.events")


class EventsCog(commands.Cog, name="Events"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── Ready ─────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        log.info("=" * 55)
        log.info(f"  Logged in as : {self.bot.user} (ID {self.bot.user.id})")
        log.info(f"  Serving      : {len(self.bot.guilds)} guild(s)")
        log.info(f"  Groq AI      : {'✓ ready' if self.bot.groq.available else '✗ unavailable'}")  # type: ignore[attr-defined]
        log.info("=" * 55)
        await self.bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="for rule breakers 🛡️",
            )
        )

    # ── Guild join ────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info(f"Joined guild: {guild.name} (ID {guild.id}, {guild.member_count} members)")
        # Seed default settings
        await queries.get_or_create_guild_settings(guild.id)
        # Send welcome message
        channel = guild.system_channel or next(
            (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages),
            None,
        )
        if channel:
            embed = discord.Embed(
                title="👋 Thanks for adding Klaud-Ninja!",
                description=(
                    "**AI Moderation & Command Execution Bot**\n\n"
                    "To get started:\n"
                    "• Set moderation intensity: `/klaud-intensity`\n"
                    "• Set a log channel: `/klaud-logchannel`\n"
                    "• Toggle AI on/off: `/klaud-ai`\n"
                    "• View settings: `/klaud-config`\n\n"
                    "Then **@mention me** with any server management instruction!"
                ),
                color=discord.Color.blurple(),
            )
            embed.set_footer(text="Default intensity: MEDIUM • AI: Enabled")
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass

    # ── Guild remove ──────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        log.info(f"Left guild: {guild.name} (ID {guild.id})")

    # ── Command errors ────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You don't have permission to use that command.", ephemeral=True)
            return
        log.error(f"Command error in {ctx.command}: {error}", exc_info=True)

    # ── App-command errors ────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
    ) -> None:
        msg = "❌ An error occurred."
        if isinstance(error, discord.app_commands.CheckFailure):
            return  # Already handled in the check decorator
        log.error(f"App command error: {error}", exc_info=True)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EventsCog(bot))
