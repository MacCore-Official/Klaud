"""
commands/server_builder.py — AI-powered Discord server builder.

Slash commands (admin only, license gated):
  /build server   instruction:<str>        — Build a server from a plain-English description
  /build template save name:<str>          — Save current server layout as a template
  /build template load name:<str>          — Restore a saved template
  /build cleanup  category:<str>           — AI-assisted channel cleanup

⚠️ All destructive operations require a confirmation step.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from ai.core import build_server_plan, admin_query
from database import db_client

log = logging.getLogger(__name__)

# Permission preset shorthands used in the plan schema
PERM_PRESETS: dict[str, discord.Permissions] = {
    "administrator": discord.Permissions(administrator=True),
    "manage_messages": discord.Permissions(manage_messages=True, read_messages=True, send_messages=True),
    "kick_members": discord.Permissions(kick_members=True, read_messages=True, send_messages=True),
    "ban_members": discord.Permissions(ban_members=True, read_messages=True, send_messages=True),
    "read_messages": discord.Permissions(read_messages=True),
    "send_messages": discord.Permissions(send_messages=True, read_messages=True),
}

ROLE_COLORS: dict[str, discord.Color] = {
    "#ff0000": discord.Color.red(),
    "#00ff00": discord.Color.green(),
    "#0000ff": discord.Color.blue(),
    "#ffff00": discord.Color.yellow(),
    "#ff6600": discord.Color.orange(),
    "#9900ff": discord.Color.purple(),
    "#00ccff": discord.Color.teal(),
}


def _resolve_color(hex_color: str) -> discord.Color:
    return ROLE_COLORS.get(hex_color.lower(), discord.Color.default())


def _resolve_permissions(perm_list: list[str]) -> discord.Permissions:
    perms = discord.Permissions.none()
    for p in perm_list:
        preset = PERM_PRESETS.get(p)
        if preset:
            perms = discord.Permissions(perms.value | preset.value)
    return perms


class ConfirmView(discord.ui.View):
    """Simple yes/no confirmation buttons."""

    def __init__(self) -> None:
        super().__init__(timeout=60)
        self.confirmed: bool = False

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = False
        self.stop()
        await interaction.response.defer()


class ServerBuilderCog(commands.Cog, name="ServerBuilder"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _licensed(self, guild_id: int) -> bool:
        lic = await db_client.get_license(guild_id)
        return lic is not None and lic.get("active", False)

    # ── /build group ──────────────────────────────────────────────────────────

    build_group = app_commands.Group(
        name="build", description="AI-powered server building tools"
    )
    template_group = app_commands.Group(
        name="template", description="Save and restore server templates", parent=build_group
    )

    # ── /build server ─────────────────────────────────────────────────────────

    @build_group.command(
        name="server",
        description='Build channels, roles, and more from a description (e.g. "a Roblox trading server")',
    )
    @app_commands.describe(instruction="Describe the server you want (one sentence is enough)")
    @app_commands.checks.has_permissions(administrator=True)
    async def build_server(self, interaction: discord.Interaction, instruction: str) -> None:
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild or not await self._licensed(interaction.guild.id):
            await interaction.followup.send("❌ No active license found.", ephemeral=True)
            return

        # Generate plan
        await interaction.followup.send("🤖 Generating server plan...", ephemeral=True)
        plan = await build_server_plan(instruction)

        if not plan:
            await interaction.followup.send("❌ AI failed to generate a plan. Try rephrasing.", ephemeral=True)
            return

        # Show plan summary and ask for confirmation
        summary = self._plan_summary(plan)
        view = ConfirmView()
        confirm_embed = discord.Embed(
            title="⚠️ Confirm Server Build",
            description=(
                f"The following will be created in **{interaction.guild.name}**:\n\n{summary}"
                f"\n\n**This cannot be undone. Confirm?**"
            ),
            color=discord.Color.yellow(),
        )
        msg = await interaction.followup.send(embed=confirm_embed, view=view, ephemeral=True)

        await view.wait()
        if not view.confirmed:
            await interaction.followup.send("🚫 Build cancelled.", ephemeral=True)
            return

        # Execute build
        await interaction.followup.send("🔨 Building server...", ephemeral=True)
        errors = await self._execute_plan(interaction.guild, plan)

        # Log
        await db_client.log_action(
            interaction.guild.id, "server_build", interaction.user.id,
            interaction.user.id, instruction[:200]
        )

        result_embed = discord.Embed(
            title="✅ Server Build Complete",
            description=f"Instruction: *{instruction}*",
            color=discord.Color.green(),
        )
        if errors:
            result_embed.add_field(
                name="⚠️ Warnings", value="\n".join(errors[:10]), inline=False
            )
        await interaction.followup.send(embed=result_embed, ephemeral=True)

    def _plan_summary(self, plan: dict) -> str:
        lines: list[str] = []
        categories = plan.get("categories", [])
        roles = plan.get("roles", [])
        lines.append(f"📁 **{len(categories)} categories**")
        total_channels = sum(len(c.get("channels", [])) for c in categories)
        lines.append(f"💬 **{total_channels} channels**")
        lines.append(f"🏷️ **{len(roles)} roles**")
        if plan.get("welcome_message"):
            lines.append("👋 Welcome message")
        if plan.get("rules"):
            lines.append(f"📜 {len(plan['rules'])} rules")
        return "\n".join(lines)

    async def _execute_plan(self, guild: discord.Guild, plan: dict) -> list[str]:
        """Create roles, categories, and channels as described in the plan. Returns error strings."""
        errors: list[str] = []
        created_roles: dict[str, discord.Role] = {}

        # Create roles first
        for role_data in plan.get("roles", []):
            try:
                perms = _resolve_permissions(role_data.get("permissions", []))
                color = _resolve_color(role_data.get("color", ""))
                role = await guild.create_role(
                    name=role_data["name"],
                    permissions=perms,
                    color=color,
                    hoist=role_data.get("hoisted", False),
                    mentionable=role_data.get("mentionable", False),
                    reason="Klaud AI Server Build",
                )
                created_roles[role_data["name"]] = role
                await asyncio.sleep(0.5)  # rate limit buffer
            except Exception as exc:
                errors.append(f"Role '{role_data.get('name', '?')}': {exc}")

        # Create categories and channels
        for cat_data in plan.get("categories", []):
            try:
                category = await guild.create_category(
                    cat_data["name"], reason="Klaud AI Server Build"
                )
                await asyncio.sleep(0.3)
            except Exception as exc:
                errors.append(f"Category '{cat_data.get('name', '?')}': {exc}")
                continue

            for ch_data in cat_data.get("channels", []):
                try:
                    ch_type = ch_data.get("type", "text")
                    kwargs: dict[str, Any] = {
                        "name": ch_data["name"],
                        "category": category,
                        "reason": "Klaud AI Server Build",
                    }
                    if ch_type == "text":
                        kwargs["topic"] = ch_data.get("topic", "")
                        kwargs["slowmode_delay"] = ch_data.get("slowmode", 0)
                        kwargs["nsfw"] = ch_data.get("nsfw", False)
                        await guild.create_text_channel(**kwargs)
                    elif ch_type == "voice":
                        await guild.create_voice_channel(**kwargs)
                    elif ch_type == "announcement":
                        await guild.create_text_channel(**kwargs, news=True)
                    elif ch_type == "forum":
                        await guild.create_forum(**kwargs)
                    await asyncio.sleep(0.3)
                except Exception as exc:
                    errors.append(f"Channel '{ch_data.get('name', '?')}': {exc}")

        # Post rules if a rules channel was created and rules exist
        rules = plan.get("rules", [])
        welcome = plan.get("welcome_message", "")
        if rules:
            rules_ch = discord.utils.find(
                lambda c: "rule" in c.name.lower(), guild.text_channels
            )
            if rules_ch:
                rules_text = "\n".join(f"{i+1}. {r}" for i, r in enumerate(rules))
                try:
                    await rules_ch.send(f"📜 **Server Rules**\n\n{rules_text}")
                except Exception:
                    pass

        if welcome:
            welcome_ch = discord.utils.find(
                lambda c: "welcome" in c.name.lower(), guild.text_channels
            )
            if welcome_ch:
                try:
                    await welcome_ch.send(welcome)
                except Exception:
                    pass

        return errors

    # ── /build template save ──────────────────────────────────────────────────

    @template_group.command(name="save", description="Save current server layout as a named template")
    @app_commands.describe(name="Template name (e.g. trading-server)")
    @app_commands.checks.has_permissions(administrator=True)
    async def template_save(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild or not await self._licensed(interaction.guild.id):
            await interaction.followup.send("❌ No active license found.", ephemeral=True)
            return

        snapshot = self._snapshot_guild(interaction.guild)
        result = await db_client.save_template(interaction.guild.id, name, snapshot)

        if result:
            await interaction.followup.send(f"✅ Template `{name}` saved ({len(snapshot['categories'])} categories).", ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed to save template.", ephemeral=True)

    def _snapshot_guild(self, guild: discord.Guild) -> dict:
        """Capture current server structure into a serialisable dict."""
        categories = []
        for cat in guild.categories:
            channels = []
            for ch in cat.channels:
                ch_data: dict[str, Any] = {"name": ch.name}
                if isinstance(ch, discord.TextChannel):
                    ch_data["type"] = "text"
                    ch_data["topic"] = ch.topic or ""
                    ch_data["slowmode"] = ch.slowmode_delay
                    ch_data["nsfw"] = ch.nsfw
                elif isinstance(ch, discord.VoiceChannel):
                    ch_data["type"] = "voice"
                else:
                    ch_data["type"] = "text"
                channels.append(ch_data)
            categories.append({"name": cat.name, "channels": channels})

        roles = []
        for role in guild.roles:
            if role.is_default():
                continue
            roles.append({
                "name": role.name,
                "color": str(role.color),
                "hoisted": role.hoist,
                "mentionable": role.mentionable,
            })

        return {"categories": categories, "roles": roles}

    # ── /build template load ──────────────────────────────────────────────────

    @template_group.command(name="load", description="Restore a previously saved server template")
    @app_commands.describe(name="Template name to load")
    @app_commands.checks.has_permissions(administrator=True)
    async def template_load(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild or not await self._licensed(interaction.guild.id):
            await interaction.followup.send("❌ No active license found.", ephemeral=True)
            return

        tmpl = await db_client.load_template(interaction.guild.id, name)
        if not tmpl:
            await interaction.followup.send(f"❌ Template `{name}` not found.", ephemeral=True)
            return

        view = ConfirmView()
        await interaction.followup.send(
            f"⚠️ This will add roles/channels from template `{name}` to the server. Existing channels will NOT be deleted. Continue?",
            view=view,
            ephemeral=True,
        )
        await view.wait()

        if not view.confirmed:
            await interaction.followup.send("🚫 Cancelled.", ephemeral=True)
            return

        errors = await self._execute_plan(interaction.guild, tmpl["template_json"])
        embed = discord.Embed(title=f"✅ Template `{name}` Applied", color=discord.Color.green())
        if errors:
            embed.add_field(name="⚠️ Warnings", value="\n".join(errors[:10]), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Error handler ──────────────────────────────────────────────────────────

    @build_server.error
    @template_save.error
    @template_load.error
    async def _perm_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
        else:
            log.error("ServerBuilder command error: %s", error)
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ An error occurred.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerBuilderCog(bot))
