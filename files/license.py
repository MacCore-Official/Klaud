"""
commands/license.py — License management slash commands.

Commands:
  /activate-license  key:<str>   — Activate a license key for this server
  /license-info                  — Show current license status
  /transfer-license  user:<User> — Transfer license ownership to another user
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from database import db_client
import config

log = logging.getLogger(__name__)


class LicenseCog(commands.Cog, name="License"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.periodic_check.start()

    def cog_unload(self) -> None:
        self.periodic_check.cancel()

    # ── Periodic license validation ────────────────────────────────────────────

    @tasks.loop(seconds=config.LICENSE_CHECK_INTERVAL)
    async def periodic_check(self) -> None:
        """
        Iterate over all guilds and log any that have expired or inactive licenses.
        Does NOT kick the bot — admins must manually remove if they wish.
        """
        for guild in self.bot.guilds:
            lic = await db_client.get_license(guild.id)
            if not lic:
                log.warning("Guild %s (%s) has no active license.", guild.id, guild.name)
                continue

            expires_at = lic.get("expires_at")
            if expires_at:
                try:
                    exp = datetime.fromisoformat(expires_at)
                    if exp < datetime.now(timezone.utc):
                        log.warning(
                            "License for guild %s expired at %s. Deactivating.",
                            guild.id,
                            expires_at,
                        )
                        await db_client.deactivate_license(guild.id)
                except ValueError:
                    pass

    @periodic_check.before_loop
    async def before_check(self) -> None:
        await self.bot.wait_until_ready()

    # ── /activate-license ──────────────────────────────────────────────────────

    @app_commands.command(name="activate-license", description="Activate a license key for this server")
    @app_commands.describe(key="Your license key")
    @app_commands.checks.has_permissions(administrator=True)
    async def activate_license(self, interaction: discord.Interaction, key: str) -> None:
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send("❌ This command must be used in a server.", ephemeral=True)
            return

        # Basic format validation — adjust pattern to match your real key format
        if len(key) < 16 or not key.replace("-", "").isalnum():
            await interaction.followup.send(
                "❌ Invalid license key format. Keys must be alphanumeric and at least 16 characters.",
                ephemeral=True,
            )
            return

        result = await db_client.activate_license(
            guild_id=interaction.guild.id,
            license_key=key,
            owner_id=interaction.user.id,
        )

        if result:
            embed = discord.Embed(
                title="✅ License Activated",
                description=f"**Server:** {interaction.guild.name}\n**Owner:** {interaction.user.mention}",
                color=discord.Color.green(),
            )
            embed.add_field(name="Key", value=f"`{key[:8]}...{key[-4:]}`")
            embed.set_footer(text="All Klaud features are now enabled.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            await db_client.log_action(
                interaction.guild.id, "license_activate", interaction.user.id, interaction.user.id, key[:8]
            )
        else:
            await interaction.followup.send("❌ Failed to save license. Check logs.", ephemeral=True)

    # ── /license-info ──────────────────────────────────────────────────────────

    @app_commands.command(name="license-info", description="Check the current license status for this server")
    @app_commands.checks.has_permissions(administrator=True)
    async def license_info(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send("❌ This command must be used in a server.", ephemeral=True)
            return

        lic = await db_client.get_license(interaction.guild.id)

        if not lic:
            embed = discord.Embed(
                title="❌ No License",
                description=(
                    "This server does not have an active Klaud license.\n"
                    "Use `/activate-license` to activate one."
                ),
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        expires = lic.get("expires_at") or "Never"
        activated = lic.get("activated_at", "Unknown")[:10]

        embed = discord.Embed(
            title="📋 License Info",
            color=discord.Color.green() if lic["active"] else discord.Color.red(),
        )
        embed.add_field(name="Status", value="✅ Active" if lic["active"] else "❌ Inactive", inline=True)
        embed.add_field(name="Owner", value=f"<@{lic['owner_id']}>", inline=True)
        embed.add_field(name="Activated", value=activated, inline=True)
        embed.add_field(name="Expires", value=str(expires)[:10] if expires != "Never" else "Never", inline=True)
        embed.set_footer(text=f"Key: {lic['license_key'][:8]}...")

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /transfer-license ──────────────────────────────────────────────────────

    @app_commands.command(name="transfer-license", description="Transfer license ownership to another user")
    @app_commands.describe(user="The new license owner")
    async def transfer_license(self, interaction: discord.Interaction, user: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send("❌ This command must be used in a server.", ephemeral=True)
            return

        lic = await db_client.get_license(interaction.guild.id)
        if not lic:
            await interaction.followup.send("❌ No active license found for this server.", ephemeral=True)
            return

        # Only the current license owner or server owner can transfer
        is_owner = interaction.user.id == interaction.guild.owner_id
        is_lic_owner = str(interaction.user.id) == str(lic["owner_id"])

        if not (is_owner or is_lic_owner):
            await interaction.followup.send(
                "❌ Only the license owner or server owner can transfer the license.", ephemeral=True
            )
            return

        ok = await db_client.transfer_license(interaction.guild.id, user.id)
        if ok:
            await interaction.followup.send(
                f"✅ License transferred to {user.mention}.", ephemeral=True
            )
            await db_client.log_action(
                interaction.guild.id, "license_transfer", user.id, interaction.user.id, ""
            )
        else:
            await interaction.followup.send("❌ Transfer failed. Check logs.", ephemeral=True)

    # ── Error handler ──────────────────────────────────────────────────────────

    @activate_license.error
    @license_info.error
    @transfer_license.error
    async def _perm_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need **Administrator** permission for license commands.", ephemeral=True
            )
        else:
            log.error("License command error: %s", error)
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LicenseCog(bot))
