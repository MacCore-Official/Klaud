"""
commands/ai_prompt.py — Admin slash commands for managing custom AI prompts.

Commands (all admin-only, license-gated):
  /ai-prompt add   name:<str> text:<str>  — Add or update a custom rule
  /ai-prompt list                         — List all custom rules for this server
  /ai-prompt remove name:<str>            — Delete a custom rule
  /ai-prompt test  message:<str>          — Test the AI moderation against a sample message
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ai.core import moderation_check
from ai.prompt_manager import prompt_manager
from database import db_client

log = logging.getLogger(__name__)


class AIPromptCog(commands.Cog, name="AIPrompt"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _licensed(self, guild_id: int) -> bool:
        lic = await db_client.get_license(guild_id)
        return lic is not None and lic.get("active", False)

    # ── Prompt group ───────────────────────────────────────────────────────────

    prompt_group = app_commands.Group(
        name="ai-prompt", description="Manage custom AI moderation rules"
    )

    @prompt_group.command(name="add", description="Add or update a custom AI rule")
    @app_commands.describe(
        name="Short name for this rule (e.g. swearing_rule)",
        text="The rule instruction (e.g. 'If someone swears, warn them and timeout if repeated')",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def prompt_add(
        self, interaction: discord.Interaction, name: str, text: str
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild or not await self._licensed(interaction.guild.id):
            await interaction.followup.send("❌ No active license found for this server.", ephemeral=True)
            return

        # Sanitise name
        name = name.lower().replace(" ", "_")[:64]

        result = await prompt_manager.add_prompt(
            guild_id=interaction.guild.id,
            name=name,
            text=text,
            created_by=interaction.user.id,
        )

        if result:
            embed = discord.Embed(
                title="✅ Custom Rule Saved",
                color=discord.Color.green(),
            )
            embed.add_field(name="Name", value=f"`{name}`", inline=False)
            embed.add_field(name="Rule", value=text[:1024], inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed to save rule. Check logs.", ephemeral=True)

    @prompt_group.command(name="list", description="List all custom AI rules for this server")
    @app_commands.checks.has_permissions(administrator=True)
    async def prompt_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild or not await self._licensed(interaction.guild.id):
            await interaction.followup.send("❌ No active license found for this server.", ephemeral=True)
            return

        prompts = await prompt_manager.get_all(interaction.guild.id)

        if not prompts:
            await interaction.followup.send(
                "ℹ️ No custom rules set. Use `/ai-prompt add` to create one.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="📋 Custom AI Rules",
            color=discord.Color.blurple(),
        )
        for p in prompts:
            embed.add_field(
                name=f"`{p['prompt_name']}`",
                value=p["prompt_text"][:256],
                inline=False,
            )
        embed.set_footer(text=f"{len(prompts)} rule(s) active")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @prompt_group.command(name="remove", description="Remove a custom AI rule")
    @app_commands.describe(name="Name of the rule to remove")
    @app_commands.checks.has_permissions(administrator=True)
    async def prompt_remove(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild or not await self._licensed(interaction.guild.id):
            await interaction.followup.send("❌ No active license found for this server.", ephemeral=True)
            return

        ok = await prompt_manager.remove_prompt(interaction.guild.id, name)
        if ok:
            await interaction.followup.send(f"🗑️ Rule `{name}` removed.", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Could not remove rule `{name}`.", ephemeral=True)

    @prompt_group.command(
        name="test", description="Test AI moderation on a sample message using current rules"
    )
    @app_commands.describe(message="The sample message to test")
    @app_commands.checks.has_permissions(administrator=True)
    async def prompt_test(self, interaction: discord.Interaction, message: str) -> None:
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild or not await self._licensed(interaction.guild.id):
            await interaction.followup.send("❌ No active license found for this server.", ephemeral=True)
            return

        rules = await prompt_manager.get_combined_rules(interaction.guild.id)
        result = await moderation_check(message, custom_rules=rules)

        color = discord.Color.green() if not result.get("violation") else discord.Color.red()
        embed = discord.Embed(title="🧪 Moderation Test Result", color=color)
        embed.add_field(name="Input", value=f"```{message[:500]}```", inline=False)
        embed.add_field(name="Violation?", value="✅ No" if not result.get("violation") else "❌ Yes", inline=True)
        embed.add_field(name="Severity", value=result.get("severity", "N/A"), inline=True)
        embed.add_field(name="Action", value=result.get("action", "none"), inline=True)
        embed.add_field(name="Categories", value=", ".join(result.get("categories", [])) or "none", inline=True)
        embed.add_field(name="Reason", value=result.get("reason", "N/A"), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Error handler ──────────────────────────────────────────────────────────

    @prompt_add.error
    @prompt_list.error
    @prompt_remove.error
    @prompt_test.error
    async def _perm_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
        else:
            log.error("AIPrompt command error: %s", error)
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ An error occurred.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AIPromptCog(bot))
