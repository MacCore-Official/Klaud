"""
moderation/warnings.py — Warning system for Klaud Bot.

Provides:
  issue_warning()       : shared helper called by automod and manual commands
  /warnings view        : list warnings for a user
  /warnings clear       : clear all warnings for a user
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from database import db_client

log = logging.getLogger(__name__)


# ── Shared helper (used by automod AND slash commands) ─────────────────────────

async def issue_warning(
    guild: discord.Guild,
    user: discord.Member | discord.User,
    moderator: discord.Member | discord.ClientUser,
    reason: str,
) -> int:
    """
    Add a warning to Supabase and return the new total warning count for this user.
    """
    await db_client.add_warning(
        guild_id=guild.id,
        user_id=user.id,
        moderator_id=moderator.id,
        reason=reason,
    )
    await db_client.log_action(
        guild_id=guild.id,
        action="warn",
        target_id=user.id,
        actor_id=moderator.id,
        detail=reason,
    )
    warnings = await db_client.get_warnings(guild.id, user.id)
    return len(warnings)


# ── Cog with slash commands ────────────────────────────────────────────────────

class Warnings(commands.Cog, name="Warnings"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # Helper: check license
    async def _licensed(self, guild_id: int) -> bool:
        lic = await db_client.get_license(guild_id)
        return lic is not None and lic.get("active", False)

    # ── /warnings ──────────────────────────────────────────────────────────────

    warnings_group = app_commands.Group(name="warnings", description="Manage user warnings")

    @warnings_group.command(name="view", description="View warnings for a user")
    @app_commands.describe(user="The member to check")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warnings_view(
        self, interaction: discord.Interaction, user: discord.Member
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild or not await self._licensed(interaction.guild.id):
            await interaction.followup.send("❌ No active license found for this server.", ephemeral=True)
            return

        warns = await db_client.get_warnings(interaction.guild.id, user.id)

        if not warns:
            await interaction.followup.send(f"✅ {user.mention} has no warnings.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"⚠️ Warnings — {user.display_name}",
            color=discord.Color.orange(),
        )
        for i, w in enumerate(warns, 1):
            embed.add_field(
                name=f"#{i} — {w['created_at'][:10]}",
                value=f"**Reason:** {w['reason']}\n**By:** <@{w['moderator_id']}>",
                inline=False,
            )
        embed.set_footer(text=f"Total: {len(warns)} warning(s)")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @warnings_group.command(name="clear", description="Clear all warnings for a user")
    @app_commands.describe(user="The member to clear")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warnings_clear(
        self, interaction: discord.Interaction, user: discord.Member
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild or not await self._licensed(interaction.guild.id):
            await interaction.followup.send("❌ No active license found for this server.", ephemeral=True)
            return

        count = await db_client.clear_warnings(interaction.guild.id, user.id)
        await db_client.log_action(
            interaction.guild.id,
            "clear_warnings",
            user.id,
            interaction.user.id,
            f"Cleared {count} warning(s)",
        )
        await interaction.followup.send(
            f"🗑️ Cleared **{count}** warning(s) for {user.mention}.", ephemeral=True
        )

    @warnings_group.command(name="add", description="Manually add a warning to a user")
    @app_commands.describe(user="The member to warn", reason="Reason for the warning")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warnings_add(
        self, interaction: discord.Interaction, user: discord.Member, reason: str
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild or not await self._licensed(interaction.guild.id):
            await interaction.followup.send("❌ No active license found for this server.", ephemeral=True)
            return

        count = await issue_warning(
            guild=interaction.guild,
            user=user,
            moderator=interaction.user,
            reason=reason,
        )

        try:
            await user.send(
                f"⚠️ You received a warning in **{interaction.guild.name}**.\n"
                f"Reason: {reason}\nTotal warnings: {count}"
            )
        except discord.Forbidden:
            pass

        await interaction.followup.send(
            f"⚠️ Warning issued to {user.mention}. Total warnings: **{count}**",
            ephemeral=True,
        )

    @warnings_view.error
    @warnings_clear.error
    @warnings_add.error
    async def _perm_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        else:
            log.error("Warnings command error: %s", error)
            await interaction.response.send_message("❌ An error occurred.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Warnings(bot))
