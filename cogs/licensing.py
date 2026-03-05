"""
KLAUD-NINJA — Licensing Cog
═══════════════════════════════════════════════════════════════════════════════
All license management commands.

User commands (require Manage Guild):
  /license redeem <key>   — Activate this server with a license key
  /license status         — Show current license status
  /license info           — Show tier comparison table

Owner-only commands:
  /license generate tier duration_days    — Generate a new key
  /license activate server_id tier days  — Instantly activate a server
  /license revoke key                    — Revoke a key
  /license disable server_id             — Disable a server's license
  /license list                          — List all active licenses
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.bot import KlaudBot

logger = logging.getLogger("klaud.licensing")

# ─── Tier metadata ────────────────────────────────────────────────────────────

TIER_COLORS = {
    "BASIC":      discord.Color.blue(),
    "PRO":        discord.Color.gold(),
    "ENTERPRISE": discord.Color.purple(),
}

TIER_EMOJIS = {
    "BASIC":      "🔵",
    "PRO":        "🟡",
    "ENTERPRISE": "🟣",
}

TIER_FEATURES = {
    "BASIC": [
        "✅ AI message moderation",
        "✅ Auto warn / delete",
        "✅ Spam detection",
        "✅ Mod action logging",
        "✅ Moderation history",
        "❌ Kick punishment",
        "❌ Ban punishment",
        "❌ AI admin commands",
        "❌ Verification system",
    ],
    "PRO": [
        "✅ Everything in BASIC",
        "✅ Kick punishment",
        "✅ AI admin commands (@mention)",
        "✅ Natural language server management",
        "✅ Priority AI processing",
        "❌ Ban punishment",
        "❌ Verification system",
    ],
    "ENTERPRISE": [
        "✅ Everything in PRO",
        "✅ Ban punishment",
        "✅ Verification system",
        "✅ Anti-bot join protection",
        "✅ Full audit logging",
        "✅ Dedicated support",
    ],
}


# ─── Cog ─────────────────────────────────────────────────────────────────────

class LicensingCog(commands.Cog, name="Licensing"):
    """
    Manages license key operations for KLAUD-NINJA.
    All moderation features gate on license validation.
    """

    def __init__(self, bot: KlaudBot) -> None:
        self.bot = bot
        logger.info("LicensingCog initialised")

    # ─── Command group ────────────────────────────────────────────────────────

    license_group = app_commands.Group(
        name="license",
        description="Manage KLAUD-NINJA licenses",
    )

    # ─── /license redeem ─────────────────────────────────────────────────────

    @license_group.command(
        name="redeem",
        description="Activate this server with a license key",
    )
    @app_commands.describe(key="Your license key (format: KLAUD-XXXX-XXXX-XXXX)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def license_redeem(
        self,
        interaction: discord.Interaction,
        key: str,
    ) -> None:
        """Redeem a license key to activate this server."""
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
            # Get the new license record for the embed
            record = await self.bot.license_manager.get_record(interaction.guild.id)
            tier = record.tier if record else "BASIC"

            embed = discord.Embed(
                title=f"{TIER_EMOJIS.get(tier, '✅')} License Activated!",
                description=(
                    f"**{interaction.guild.name}** is now running on a **{tier}** license.\n\n"
                    f"{message}"
                ),
                color=TIER_COLORS.get(tier, discord.Color.green()),
            )
            if record:
                embed.add_field(
                    name="Tier",
                    value=f"{TIER_EMOJIS.get(tier, '')} {tier}",
                    inline=True,
                )
                embed.add_field(
                    name="Expires",
                    value=(
                        f"<t:{int(record.expires_at.timestamp())}:R>"
                        if record.expires_at
                        else "Never (lifetime)"
                    ),
                    inline=True,
                )
                embed.add_field(
                    name="Activated by",
                    value=interaction.user.mention,
                    inline=True,
                )
            embed.set_footer(text="Use /license status to view details anytime")
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            embed = discord.Embed(
                title="❌ License Redemption Failed",
                description=message,
                color=discord.Color.red(),
            )
            embed.set_footer(text="Contact the bot owner if you need assistance")
            await interaction.followup.send(embed=embed, ephemeral=True)

        logger.info(
            f"License redeem attempt | guild={interaction.guild.id} | "
            f"key={key[:12]}... | success={success} | by={interaction.user.id}"
        )

    # ─── /license status ─────────────────────────────────────────────────────

    @license_group.command(
        name="status",
        description="View the current license status of this server",
    )
    async def license_status(self, interaction: discord.Interaction) -> None:
        """Show the current license status of this server."""
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send("❌ Server only.", ephemeral=True)
            return

        record = await self.bot.license_manager.get_record(interaction.guild.id)
        is_owner_server = self.bot.settings.is_owner_server(interaction.guild.id)

        if is_owner_server:
            embed = discord.Embed(
                title="⭐ Owner Test Server",
                description=(
                    "This is the bot owner's test server and is permanently licensed "
                    "at **ENTERPRISE** level."
                ),
                color=discord.Color.gold(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        if not record or not record.is_valid:
            embed = discord.Embed(
                title="⛔ No Active License",
                description=(
                    "This server does not have an active license.\n\n"
                    "Use `/license redeem <key>` to activate this server.\n"
                    "Use `/license info` to compare license tiers."
                ),
                color=discord.Color.red(),
            )
            if record and not record.active:
                embed.add_field(
                    name="Previous License",
                    value=f"A `{record.tier}` license was previously active but has been disabled.",
                    inline=False,
                )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Active license
        tier = record.tier
        embed = discord.Embed(
            title=f"{TIER_EMOJIS.get(tier, '✅')} License Active — {tier}",
            description=f"**{interaction.guild.name}** has an active **{tier}** license.",
            color=TIER_COLORS.get(tier, discord.Color.green()),
        )
        embed.add_field(name="Tier", value=f"{TIER_EMOJIS.get(tier, '')} {tier}", inline=True)
        embed.add_field(
            name="Expires",
            value=(
                f"<t:{int(record.expires_at.timestamp())}:R>"
                if record.expires_at else "Never (lifetime)"
            ),
            inline=True,
        )
        embed.add_field(
            name="Redeemed",
            value=(
                f"<t:{int(record.redeemed_at.timestamp())}:D>"
                if record.redeemed_at else "Unknown"
            ),
            inline=True,
        )

        # Days remaining warning
        days = record.days_remaining
        if days is not None and days <= 7:
            embed.add_field(
                name="⚠️ Expiry Warning",
                value=f"This license expires in **{days} day(s)**. Contact the bot owner to renew.",
                inline=False,
            )

        # Feature list
        features = TIER_FEATURES.get(tier, [])
        if features:
            embed.add_field(
                name="Features",
                value="\n".join(features[:6]),
                inline=False,
            )

        embed.set_footer(text=f"Key: {record.license_key[:14]}...")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── /license info ───────────────────────────────────────────────────────

    @license_group.command(
        name="info",
        description="Compare license tiers",
    )
    async def license_info(self, interaction: discord.Interaction) -> None:
        """Display a comparison table of all license tiers."""
        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title="📋 KLAUD-NINJA License Tiers",
            description=(
                "All tiers include AI moderation, automatic punishments, "
                "mod logging, and rule-based fallback when AI is offline."
            ),
            color=discord.Color.blurple(),
        )

        for tier, features in TIER_FEATURES.items():
            emoji = TIER_EMOJIS.get(tier, "")
            embed.add_field(
                name=f"{emoji} {tier}",
                value="\n".join(features),
                inline=True,
            )

        embed.add_field(
            name="\u200b",
            value="Contact the bot owner to purchase a license key.",
            inline=False,
        )
        embed.set_footer(text="KLAUD-NINJA • License-Only AI Moderation")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── /license generate (owner only) ──────────────────────────────────────

    @license_group.command(
        name="generate",
        description="[OWNER] Generate a new license key",
    )
    @app_commands.describe(
        tier="License tier: BASIC, PRO, or ENTERPRISE",
        duration_days="Days until expiry (0 = never expires)",
    )
    async def license_generate(
        self,
        interaction: discord.Interaction,
        tier: str = "BASIC",
        duration_days: int = 0,
    ) -> None:
        """Generate a new license key. Owner only."""
        if interaction.user.id != self.bot.settings.BOT_OWNER_ID:
            await interaction.response.send_message("❌ Owner only.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        tier = tier.strip().upper()
        if tier not in ("BASIC", "PRO", "ENTERPRISE"):
            await interaction.followup.send(
                "❌ Invalid tier. Choose: `BASIC`, `PRO`, or `ENTERPRISE`",
                ephemeral=True,
            )
            return

        try:
            days: Optional[int] = duration_days if duration_days > 0 else None
            key = await self.bot.license_manager.generate_key(
                tier=tier,
                duration_days=days,
                created_by=interaction.user.id,
            )
        except Exception as exc:
            await interaction.followup.send(f"❌ Failed to generate key: {exc}", ephemeral=True)
            logger.error(f"License generate error: {exc}", exc_info=True)
            return

        embed = discord.Embed(
            title=f"{TIER_EMOJIS.get(tier, '🔑')} License Key Generated",
            color=TIER_COLORS.get(tier, discord.Color.green()),
        )
        embed.add_field(name="Key", value=f"```{key}```", inline=False)
        embed.add_field(name="Tier", value=tier, inline=True)
        embed.add_field(
            name="Duration",
            value=f"{duration_days} days" if duration_days > 0 else "Lifetime",
            inline=True,
        )
        embed.add_field(
            name="Status",
            value="Unbound — ready to redeem with `/license redeem`",
            inline=False,
        )
        embed.set_footer(text="Keep this key private — share only with the server owner")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── /license activate (owner only) ──────────────────────────────────────

    @license_group.command(
        name="activate",
        description="[OWNER] Instantly activate a server without requiring key redemption",
    )
    @app_commands.describe(
        server_id="The Discord server ID to activate",
        tier="License tier: BASIC, PRO, or ENTERPRISE",
        duration_days="Days until expiry (0 = never expires)",
    )
    async def license_activate(
        self,
        interaction: discord.Interaction,
        server_id: str,
        tier: str = "BASIC",
        duration_days: int = 0,
    ) -> None:
        """Instantly activate a server without key redemption. Owner only."""
        if interaction.user.id != self.bot.settings.BOT_OWNER_ID:
            await interaction.response.send_message("❌ Owner only.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            gid = int(server_id.strip())
        except ValueError:
            await interaction.followup.send(
                "❌ Invalid server ID — must be a numeric Discord guild ID.",
                ephemeral=True,
            )
            return

        tier = tier.strip().upper()
        if tier not in ("BASIC", "PRO", "ENTERPRISE"):
            await interaction.followup.send(
                "❌ Invalid tier. Choose: `BASIC`, `PRO`, or `ENTERPRISE`",
                ephemeral=True,
            )
            return

        days: Optional[int] = duration_days if duration_days > 0 else None

        success, message, key = await self.bot.license_manager.activate_server(
            guild_id=gid,
            tier=tier,
            duration_days=days,
            activated_by=interaction.user.id,
        )

        if success:
            embed = discord.Embed(
                title=f"{TIER_EMOJIS.get(tier, '✅')} Server Activated",
                description=message,
                color=TIER_COLORS.get(tier, discord.Color.green()),
            )
            embed.add_field(name="Server ID", value=str(gid), inline=True)
            embed.add_field(name="Tier", value=tier, inline=True)
            embed.add_field(
                name="Expires",
                value=f"{duration_days} days" if days else "Never",
                inline=True,
            )
            if key:
                embed.add_field(name="Generated Key", value=f"```{key}```", inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(
                discord.Embed(title="❌ Activation Failed", description=message, color=discord.Color.red()),
                ephemeral=True,
            )

        logger.info(
            f"Server activate | guild={gid} | tier={tier} | "
            f"success={success} | by={interaction.user.id}"
        )

    # ─── /license revoke (owner only) ────────────────────────────────────────

    @license_group.command(
        name="revoke",
        description="[OWNER] Revoke a license key",
    )
    @app_commands.describe(key="The license key to revoke (KLAUD-XXXX-XXXX-XXXX)")
    async def license_revoke(
        self,
        interaction: discord.Interaction,
        key: str,
    ) -> None:
        """Revoke a license key by its string. Owner only."""
        if interaction.user.id != self.bot.settings.BOT_OWNER_ID:
            await interaction.response.send_message("❌ Owner only.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        success, message = await self.bot.license_manager.revoke_key(key)

        color = discord.Color.green() if success else discord.Color.red()
        embed = discord.Embed(
            title="🔑 License Revoke",
            description=message,
            color=color,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── /license disable (owner only) ───────────────────────────────────────

    @license_group.command(
        name="disable",
        description="[OWNER] Disable all licenses for a specific server",
    )
    @app_commands.describe(server_id="The Discord server ID to disable")
    async def license_disable(
        self,
        interaction: discord.Interaction,
        server_id: str,
    ) -> None:
        """Disable all active licenses for a server. Owner only."""
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

        color = discord.Color.orange() if success else discord.Color.red()
        embed = discord.Embed(
            title="🚫 Server License Disabled",
            description=message,
            color=color,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── /license list (owner only) ──────────────────────────────────────────

    @license_group.command(
        name="list",
        description="[OWNER] List active licenses",
    )
    @app_commands.describe(show_all="Show inactive/expired licenses too")
    async def license_list(
        self,
        interaction: discord.Interaction,
        show_all: bool = False,
    ) -> None:
        """List licenses in the database. Owner only."""
        if interaction.user.id != self.bot.settings.BOT_OWNER_ID:
            await interaction.response.send_message("❌ Owner only.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            records = await self.bot.license_manager.list_licenses(
                active_only=not show_all,
                limit=20,
            )
        except Exception as exc:
            await interaction.followup.send(f"❌ Database error: {exc}", ephemeral=True)
            logger.error(f"License list error: {exc}", exc_info=True)
            return

        if not records:
            await interaction.followup.send(
                "📋 No licenses found.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"📋 Licenses ({'all' if show_all else 'active'}, max 20)",
            color=discord.Color.blurple(),
        )

        lines = []
        for row in records:
            key     = row.get("license_key", "?")
            tier    = row.get("tier", "?")
            server  = row.get("server_id")
            exp     = row.get("expires_at")
            active  = row.get("active", False)

            status = "✅" if active else "❌"
            exp_str = str(exp)[:10] if exp else "lifetime"
            server_str = f"`{server}`" if server else "unbound"

            lines.append(
                f"{status} `{key}` • {TIER_EMOJIS.get(tier, '')} {tier} "
                f"• Server: {server_str} • Exp: {exp_str}"
            )

        # Split into chunks if too many
        chunk_size = 10
        for i in range(0, len(lines), chunk_size):
            chunk = lines[i:i + chunk_size]
            embed.add_field(
                name=f"Keys {i + 1}–{i + len(chunk)}",
                value="\n".join(chunk),
                inline=False,
            )

        embed.set_footer(text=f"Total shown: {len(records)}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── Error handlers ───────────────────────────────────────────────────────

    @license_redeem.error
    async def on_redeem_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need the **Manage Server** permission to redeem licenses.",
                ephemeral=True,
            )
        else:
            logger.error(f"License redeem error: {error}", exc_info=True)


async def setup(bot: KlaudBot) -> None:
    await bot.add_cog(LicensingCog(bot))
