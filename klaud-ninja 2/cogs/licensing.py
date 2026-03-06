"""
KLAUD-NINJA — Licensing Cog
Handles all /license commands:
  User:  /license redeem  /license status  /license info
  Owner: /license generate  /license revoke  /license disable  /license list
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.bot import KlaudBot

logger = logging.getLogger("klaud.licensing")


class LicensingCog(commands.Cog, name="Licensing"):
    """License management — the gateway to all bot functionality."""

    def __init__(self, bot: KlaudBot) -> None:
        self.bot = bot

    # ─── Command group ────────────────────────────────────────────────────────

    license_group = app_commands.Group(
        name="license",
        description="Manage KLAUD-NINJA licenses",
    )

    # ─── /license redeem ──────────────────────────────────────────────────────

    @license_group.command(name="redeem", description="Redeem a license key to activate KLAUD in this server")
    @app_commands.describe(key="Your license key (format: KLAUD-XXXX-XXXX-XXXX)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def license_redeem(self, interaction: discord.Interaction, key: str) -> None:
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send("❌ This command must be used in a server.", ephemeral=True)
            return

        success, message = await self.bot.license_manager.redeem_key(
            key=key,
            guild_id=interaction.guild.id,
            redeemed_by=interaction.user.id,
        )

        if success:
            # Fetch the record to show tier
            record = await self.bot.license_manager.get_record(interaction.guild.id)
            tier = record.tier if record else "BASIC"

            embed = discord.Embed(
                title="✅ License Activated!",
                description=message,
                color=discord.Color.green(),
            )
            embed.add_field(name="Server", value=interaction.guild.name, inline=True)
            embed.add_field(name="Tier", value=tier, inline=True)
            if record and record.expires_at:
                embed.add_field(
                    name="Expires",
                    value=discord.utils.format_dt(record.expires_at, style="R"),
                    inline=True,
                )
            else:
                embed.add_field(name="Expires", value="Never", inline=True)
            embed.set_footer(text="KLAUD-NINJA is now active in this server.")

            logger.info(
                f"License redeemed | guild={interaction.guild.id} "
                f"({interaction.guild.name}) | tier={tier} | by={interaction.user.id}"
            )
        else:
            embed = discord.Embed(
                title="❌ Redemption Failed",
                description=message,
                color=discord.Color.red(),
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── /license status ──────────────────────────────────────────────────────

    @license_group.command(name="status", description="Check this server's license status")
    async def license_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send("❌ This command must be used in a server.", ephemeral=True)
            return

        record = await self.bot.license_manager.get_record(interaction.guild.id)

        if record is None or not record.is_valid:
            embed = discord.Embed(
                title="🔒 No Active License",
                description=self.bot.license_manager.UNLICENSED_MESSAGE,
                color=discord.Color.red(),
            )
            embed.set_footer(text="Contact the bot owner to obtain a license key.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Valid license
        color_map = {
            "BASIC": discord.Color.blue(),
            "PRO": discord.Color.gold(),
            "ENTERPRISE": discord.Color.purple(),
        }
        color = color_map.get(record.tier, discord.Color.green())

        embed = discord.Embed(
            title="✅ License Active",
            color=color,
        )
        embed.add_field(name="Tier", value=f"**{record.tier}**", inline=True)
        embed.add_field(
            name="Status",
            value="🟢 Active" if record.active else "🔴 Disabled",
            inline=True,
        )

        if record.expires_at:
            embed.add_field(
                name="Expires",
                value=discord.utils.format_dt(record.expires_at, style="R"),
                inline=True,
            )
        else:
            embed.add_field(name="Expires", value="Never (lifetime)", inline=True)

        if record.redeemed_at:
            embed.add_field(
                name="Activated",
                value=discord.utils.format_dt(record.redeemed_at, style="D"),
                inline=True,
            )

        embed.set_footer(text=f"Key: {record.license_key[:12]}***")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── /license info ────────────────────────────────────────────────────────

    @license_group.command(name="info", description="View feature details for each license tier")
    async def license_info(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="📋 KLAUD-NINJA License Tiers",
            description="Compare what's included with each tier:",
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="🔵 BASIC",
            value=(
                "• AI message moderation (MEDIUM intensity)\n"
                "• Auto-warn & auto-delete\n"
                "• Mod action logging\n"
                "• `/license` commands"
            ),
            inline=False,
        )

        embed.add_field(
            name="🟡 PRO",
            value=(
                "• Everything in BASIC\n"
                "• All moderation intensities (LOW → EXTREME)\n"
                "• AI admin commands (@Klaud)\n"
                "• Timeout & kick punishments\n"
                "• Advanced spam detection"
            ),
            inline=False,
        )

        embed.add_field(
            name="🟣 ENTERPRISE",
            value=(
                "• Everything in PRO\n"
                "• Ban punishments\n"
                "• Verification system\n"
                "• Priority AI processing\n"
                "• Full audit log history"
            ),
            inline=False,
        )

        embed.set_footer(text="Contact the bot owner to purchase a license.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─── Owner-only commands ──────────────────────────────────────────────────

    @license_group.command(
        name="generate",
        description="[OWNER] Generate a new license key",
    )
    @app_commands.describe(
        tier="License tier: BASIC, PRO, or ENTERPRISE",
        duration_days="License duration in days (0 = never expires)",
    )
    async def license_generate(
        self,
        interaction: discord.Interaction,
        tier: str,
        duration_days: int = 0,
    ) -> None:
        if interaction.user.id != self.bot.settings.BOT_OWNER_ID:
            await interaction.response.send_message("❌ Owner only.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        tier = tier.strip().upper()
        days: Optional[int] = duration_days if duration_days > 0 else None

        try:
            key = await self.bot.license_manager.generate_key(
                tier=tier,
                duration_days=days,
                created_by=interaction.user.id,
            )
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

        embed = discord.Embed(
            title="🔑 License Key Generated",
            color=discord.Color.green(),
        )
        embed.add_field(name="Key", value=f"```{key}```", inline=False)
        embed.add_field(name="Tier", value=tier, inline=True)
        embed.add_field(
            name="Duration",
            value=f"{duration_days} days" if days else "Lifetime",
            inline=True,
        )
        embed.set_footer(text="Send this key to the server owner to redeem.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @license_group.command(
        name="revoke",
        description="[OWNER] Revoke a license key by key string",
    )
    @app_commands.describe(key="The license key to revoke")
    async def license_revoke(self, interaction: discord.Interaction, key: str) -> None:
        if interaction.user.id != self.bot.settings.BOT_OWNER_ID:
            await interaction.response.send_message("❌ Owner only.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        success, message = await self.bot.license_manager.revoke_key(key)
        color = discord.Color.green() if success else discord.Color.red()
        await interaction.followup.send(
            embed=discord.Embed(description=message, color=color),
            ephemeral=True,
        )

    @license_group.command(
        name="activate",
        description="[OWNER] Instantly activate a license for a server without a key",
    )
    @app_commands.describe(
        server_id="The Discord server ID to activate",
        tier="License tier: BASIC, PRO, or ENTERPRISE",
        duration_days="Duration in days (0 = never expires)",
    )
    async def license_activate(
        self,
        interaction: discord.Interaction,
        server_id: str,
        tier: str = "BASIC",
        duration_days: int = 0,
    ) -> None:
        if interaction.user.id != self.bot.settings.BOT_OWNER_ID:
            await interaction.response.send_message("❌ Owner only.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            gid = int(server_id.strip())
        except ValueError:
            await interaction.followup.send("❌ Invalid server ID.", ephemeral=True)
            return

        tier = tier.strip().upper()
        if tier not in ("BASIC", "PRO", "ENTERPRISE"):
            await interaction.followup.send("❌ Invalid tier. Use BASIC, PRO, or ENTERPRISE.", ephemeral=True)
            return

        # Check if already licensed
        existing = await self.bot.license_manager.get_record(gid)
        if existing and existing.is_valid:
            await interaction.followup.send(
                f"⚠️ Server `{gid}` already has an active **{existing.tier}** license.",
                ephemeral=True,
            )
            return

        # Generate a key and immediately bind it to the server
        from datetime import timedelta
        days: Optional[int] = duration_days if duration_days > 0 else None
        key = await self.bot.license_manager.generate_key(
            tier=tier,
            duration_days=days,
            created_by=interaction.user.id,
        )

        # Directly bind it
        success, message = await self.bot.license_manager.redeem_key(
            key=key,
            guild_id=gid,
            redeemed_by=interaction.user.id,
        )

        if success:
            embed = discord.Embed(
                title="✅ Server Activated",
                color=discord.Color.green(),
            )
            embed.add_field(name="Server ID", value=str(gid), inline=True)
            embed.add_field(name="Tier", value=tier, inline=True)
            embed.add_field(
                name="Expires",
                value=f"{duration_days} days" if days else "Never",
                inline=True,
            )
            embed.add_field(name="Key", value=f"`{key}`", inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"Auto-activated | guild={gid} | tier={tier} | by={interaction.user.id}")
        else:
            await interaction.followup.send(f"❌ Failed: {message}", ephemeral=True)

    @license_group.command(
        name="disable",
        description="[OWNER] Disable the license for a specific server",
    )
    @app_commands.describe(server_id="The Discord server ID to disable")
    async def license_disable(self, interaction: discord.Interaction, server_id: str) -> None:
        if interaction.user.id != self.bot.settings.BOT_OWNER_ID:
            await interaction.response.send_message("❌ Owner only.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            gid = int(server_id.strip())
        except ValueError:
            await interaction.followup.send("❌ Invalid server ID.", ephemeral=True)
            return

        success, message = await self.bot.license_manager.disable_server(gid)
        color = discord.Color.green() if success else discord.Color.red()
        await interaction.followup.send(
            embed=discord.Embed(description=message, color=color),
            ephemeral=True,
        )

    @license_group.command(
        name="list",
        description="[OWNER] List all active licenses",
    )
    @app_commands.describe(show_all="Show inactive/expired licenses too")
    async def license_list(
        self,
        interaction: discord.Interaction,
        show_all: bool = False,
    ) -> None:
        if interaction.user.id != self.bot.settings.BOT_OWNER_ID:
            await interaction.response.send_message("❌ Owner only.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        records = await self.bot.license_manager.list_licenses(
            active_only=not show_all, limit=20
        )

        if not records:
            await interaction.followup.send("No licenses found.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"📋 Licenses ({'all' if show_all else 'active only'}, max 20)",
            color=discord.Color.blurple(),
        )

        lines = []
        for r in records:
            status = "✅" if r.get("active") else "❌"
            server = r.get("server_id") or "—"
            tier = r.get("tier", "?")
            key = r.get("license_key", "?")
            expires = r.get("expires_at")
            exp_str = str(expires)[:10] if expires else "never"
            lines.append(f"{status} `{key}` • **{tier}** • Server: `{server}` • Exp: {exp_str}")

        embed.description = "\n".join(lines)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── Error handlers ───────────────────────────────────────────────────────

    @license_redeem.error
    async def redeem_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need **Manage Server** permission to redeem a license.",
                ephemeral=True,
            )
        else:
            logger.error(f"Redeem error: {error}", exc_info=True)
            await KlaudBot._safe_respond(interaction, "❌ An error occurred.", ephemeral=True)


async def setup(bot: KlaudBot) -> None:
    await bot.add_cog(LicensingCog(bot))
