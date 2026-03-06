"""
KLAUD-NINJA — Config Cog
Slash commands for guild admins to configure the bot.

Commands:
  /klaud-intensity   — Set moderation level (LOW/MEDIUM/HIGH/EXTREME)
  /klaud-ai          — Toggle AI on/off
  /klaud-logchannel  — Set the mod-log channel
  /klaud-config      — Show current settings
  /klaud-infractions — View a user's infraction history
  /klaud-ailogs      — View recent AI action log (admin)
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from database        import queries
from utils.permissions import admin_only, is_bot_owner

log = logging.getLogger("klaud.config")

_INTENSITY_CHOICES = [
    app_commands.Choice(name="LOW — severe violations only",    value="LOW"),
    app_commands.Choice(name="MEDIUM — obvious violations",     value="MEDIUM"),
    app_commands.Choice(name="HIGH — mild violations",          value="HIGH"),
    app_commands.Choice(name="EXTREME — zero tolerance",        value="EXTREME"),
]


class ConfigCog(commands.Cog, name="Config"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /klaud-intensity ──────────────────────────────────────────────────────

    @app_commands.command(
        name="klaud-intensity",
        description="Set the AI moderation intensity level for this server",
    )
    @app_commands.describe(level="How aggressively to moderate messages")
    @app_commands.choices(level=_INTENSITY_CHOICES)
    @admin_only()
    async def klaud_intensity(
        self,
        interaction: discord.Interaction,
        level: app_commands.Choice[str],
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        ok = await queries.upsert_guild_settings(
            interaction.guild.id,
            moderation_level=level.value,
        )
        if ok:
            await interaction.followup.send(
                f"✅ Moderation intensity set to **{level.name}**.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send("❌ Database error — try again.", ephemeral=True)

    # ── /klaud-ai ─────────────────────────────────────────────────────────────

    @app_commands.command(
        name="klaud-ai",
        description="Enable or disable AI moderation for this server",
    )
    @app_commands.describe(toggle="on = enable | off = disable")
    @app_commands.choices(toggle=[
        app_commands.Choice(name="on",  value="on"),
        app_commands.Choice(name="off", value="off"),
    ])
    @admin_only()
    async def klaud_ai(
        self,
        interaction: discord.Interaction,
        toggle: app_commands.Choice[str],
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        enabled = toggle.value == "on"
        ok = await queries.upsert_guild_settings(
            interaction.guild.id,
            ai_enabled=enabled,
        )
        icon = "✅" if enabled else "⛔"
        label = "enabled" if enabled else "disabled"
        if ok:
            await interaction.followup.send(
                f"{icon} AI moderation {label} for **{interaction.guild.name}**.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send("❌ Database error.", ephemeral=True)

    # ── /klaud-logchannel ─────────────────────────────────────────────────────

    @app_commands.command(
        name="klaud-logchannel",
        description="Set the channel where moderation logs are posted",
    )
    @app_commands.describe(channel="The text channel to use for mod logs")
    @admin_only()
    async def klaud_logchannel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        ok = await queries.upsert_guild_settings(
            interaction.guild.id,
            log_channel=str(channel.id),
        )
        if ok:
            await interaction.followup.send(
                f"✅ Mod-log channel set to {channel.mention}.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send("❌ Database error.", ephemeral=True)

    # ── /klaud-config ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="klaud-config",
        description="Show the current Klaud configuration for this server",
    )
    @admin_only()
    async def klaud_config(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        settings = await queries.get_or_create_guild_settings(interaction.guild.id)

        log_ch_id = settings.get("log_channel")
        log_ch    = f"<#{log_ch_id}>" if log_ch_id else "*(not set)*"
        ai_status = "✅ Enabled" if settings.get("ai_enabled", True) else "⛔ Disabled"

        embed = discord.Embed(
            title=f"⚙️ Klaud Config — {interaction.guild.name}",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="AI Moderation",  value=ai_status,                              inline=True)
        embed.add_field(name="Intensity",       value=settings.get("moderation_level", "MEDIUM"), inline=True)
        embed.add_field(name="Log Channel",     value=log_ch,                                inline=True)
        embed.set_footer(text=f"Guild ID: {interaction.guild.id} • Klaud-Ninja")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /klaud-infractions ────────────────────────────────────────────────────

    @app_commands.command(
        name="klaud-infractions",
        description="View moderation infractions for a user",
    )
    @app_commands.describe(user="The member to look up")
    @admin_only()
    async def klaud_infractions(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        records = await queries.get_user_infractions(interaction.guild.id, user.id, limit=10)

        embed = discord.Embed(
            title=f"📋 Infractions — {user.display_name}",
            color=discord.Color.orange(),
        )
        if not records:
            embed.description = "No infractions found."
        else:
            lines = []
            for r in records:
                ts     = str(r.get("timestamp", "?"))[:16]
                action = str(r.get("action", "?")).upper()
                reason = str(r.get("reason", "?"))[:80]
                lines.append(f"`{ts}` **{action}** — {reason}")
            embed.description = "\n".join(lines)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"Showing last {len(records)} records")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /klaud-ailogs ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="klaud-ailogs",
        description="Show recent AI action log for this server (admin only)",
    )
    @admin_only()
    async def klaud_ailogs(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        logs = await queries.get_ai_logs(interaction.guild.id, limit=10)

        embed = discord.Embed(
            title="🤖 AI Action Log",
            color=discord.Color.blurple(),
        )
        if not logs:
            embed.description = "No AI actions recorded yet."
        else:
            lines = []
            for entry in logs:
                ts    = str(entry.get("timestamp", "?"))[:16]
                inp   = str(entry.get("input", "?"))[:60]
                acted = str(entry.get("executed_action", "?"))[:60]
                lines.append(f"`{ts}` **In:** {inp}\n→ {acted}")
            embed.description = "\n\n".join(lines)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ConfigCog(bot))
