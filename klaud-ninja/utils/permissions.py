"""
KLAUD-NINJA — Permission Utilities
Centralised helpers for checking Discord permissions and ownership.
All AI-command gates go through these functions.
"""

from __future__ import annotations

import os
from typing import Optional

import discord
from discord import app_commands

# Global bot owner from .env (set by main.py after load_dotenv)
OWNER_ID: int = int(os.getenv("BOT_OWNER_ID", "1269145029943758899"))


# ── Checks ────────────────────────────────────────────────────────────────────

def is_bot_owner(user: discord.User | discord.Member) -> bool:
    """Return True if user is the global bot owner."""
    return user.id == OWNER_ID


def is_guild_admin(member: discord.Member) -> bool:
    """Return True if member has Administrator or Manage Guild permission."""
    return (
        member.guild_permissions.administrator
        or member.guild_permissions.manage_guild
    )


def is_moderator(member: discord.Member) -> bool:
    """Return True if member can manage messages (minimum moderator)."""
    return (
        is_guild_admin(member)
        or member.guild_permissions.manage_messages
    )


def can_moderate_target(
    bot_member: discord.Member,
    target:     discord.Member,
) -> tuple[bool, str]:
    """
    Check whether the bot can apply punishment to the target.
    Returns (can_act: bool, reason_if_not: str).
    """
    if target.guild_permissions.administrator:
        return False, "Cannot moderate an administrator."
    if target.top_role >= bot_member.top_role:
        return False, "Target's role is equal to or higher than mine."
    return True, ""


# ── app_commands decorators ───────────────────────────────────────────────────

def admin_only() -> app_commands.check:
    """Slash-command check: invoker must be guild admin or bot owner."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id == OWNER_ID:
            return True
        member = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
        if member and is_guild_admin(member):
            return True
        await interaction.response.send_message(
            "❌ You need **Administrator** or **Manage Server** permission.",
            ephemeral=True,
        )
        return False
    return app_commands.check(predicate)


def owner_only() -> app_commands.check:
    """Slash-command check: invoker must be the global bot owner."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id == OWNER_ID:
            return True
        await interaction.response.send_message(
            "❌ This command is restricted to the bot owner.",
            ephemeral=True,
        )
        return False
    return app_commands.check(predicate)
