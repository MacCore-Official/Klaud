"""
KLAUD-NINJA — Verification Cog
Button-based user verification with anti-bot delay, role assignment, and logging.
Requires ENTERPRISE license for full access.
Provides /verify setup and a persistent VerificationView for the button.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.bot import KlaudBot

logger = logging.getLogger("klaud.verify")

# Minimum seconds the user must wait before clicking verify (anti-bot)
_ANTI_BOT_DELAY = 3.0

# How long the bot will remember that a user started the verification flow
_FLOW_TIMEOUT = 120.0


class VerificationView(discord.ui.View):
    """
    Persistent verification button view.
    Sent by /verify setup and remains active across bot restarts.
    """

    def __init__(self, role_id: int) -> None:
        # timeout=None → persistent across restarts
        super().__init__(timeout=None)
        self.role_id = role_id
        # Track when each user first interacted to enforce anti-bot delay
        self._started_at: dict[int, float] = {}

    @discord.ui.button(
        label="✅ Verify Me",
        style=discord.ButtonStyle.success,
        custom_id="klaud_verify_button",
    )
    async def verify_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        member = interaction.user
        guild = interaction.guild

        if not guild or not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "❌ Something went wrong. Please try again.", ephemeral=True
            )
            return

        # Check if already has the role
        role = guild.get_role(self.role_id)
        if role and role in member.roles:
            await interaction.response.send_message(
                "✅ You are already verified!", ephemeral=True
            )
            return

        # Anti-bot delay — first interaction records time, second executes
        uid = member.id
        now = time.monotonic()

        if uid not in self._started_at:
            self._started_at[uid] = now
            await interaction.response.send_message(
                f"🤖 Anti-bot check: please click **Verify Me** again in {int(_ANTI_BOT_DELAY)} seconds.",
                ephemeral=True,
            )
            # Clean up stale entry after timeout
            asyncio.get_event_loop().call_later(
                _FLOW_TIMEOUT,
                lambda: self._started_at.pop(uid, None),
            )
            return

        elapsed = now - self._started_at.pop(uid)
        if elapsed < _ANTI_BOT_DELAY:
            await interaction.response.send_message(
                f"⏱️ Too fast! Please wait {_ANTI_BOT_DELAY:.0f} seconds and try again.",
                ephemeral=True,
            )
            return

        # Assign the role
        if not role:
            await interaction.response.send_message(
                "❌ Verification role not found. Please contact an admin.",
                ephemeral=True,
            )
            return

        try:
            await member.add_roles(role, reason="KLAUD verification")
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to assign roles. Please contact an admin.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ You've been verified and granted the **{role.name}** role! Welcome to {guild.name}.",
            ephemeral=True,
        )

        logger.info(
            f"Verified | guild={guild.id} ({guild.name}) | user={member.id} ({member}) | role={role.id}"
        )

        # Log to mod-log if available
        await self._log_verification(interaction, member, guild, role)

    async def _log_verification(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        guild: discord.Guild,
        role: discord.Role,
    ) -> None:
        """Write a verification event to the mod-log channel."""
        # We don't have bot reference in a pure View, so look it up from the interaction client
        bot = interaction.client
        if not hasattr(bot, "get_mod_log_channel"):
            return

        log_channel = await bot.get_mod_log_channel(guild)
        if not log_channel:
            return

        embed = discord.Embed(
            title="🔐 Member Verified",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Role Assigned", value=role.name, inline=True)
        embed.add_field(
            name="Account Age",
            value=discord.utils.format_dt(member.created_at, style="R"),
            inline=True,
        )
        embed.set_footer(text="KLAUD-NINJA Verification")

        try:
            await log_channel.send(embed=embed)
        except Exception:
            pass


class SetupVerifyCog(commands.Cog, name="SetupVerify"):
    """Verification system setup and management."""

    def __init__(self, bot: KlaudBot) -> None:
        self.bot = bot
        # Re-register persistent views on bot restart
        self._restore_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        """Called when cog is loaded — restore persistent views from DB."""
        self._restore_task = asyncio.create_task(self._restore_persistent_views())

    async def cog_unload(self) -> None:
        if self._restore_task and not self._restore_task.done():
            self._restore_task.cancel()

    async def _restore_persistent_views(self) -> None:
        """Re-add persistent VerificationViews for all guilds that have verification set up."""
        await self.bot.wait_until_ready()
        await asyncio.sleep(2)  # Brief delay to ensure DB is ready

        try:
            if self.bot.db.is_postgres():
                rows = await self.bot.db.fetch(
                    "SELECT guild_id, verification_role_id FROM guild_settings "
                    "WHERE verification_role_id IS NOT NULL"
                )
            elif self.bot.db.is_sqlite():
                rows = await self.bot.db.fetch(
                    None,
                    sqlite_query=(
                        "SELECT guild_id, verification_role_id FROM guild_settings "
                        "WHERE verification_role_id IS NOT NULL"
                    ),
                )
            else:
                rows = []

            for row in rows:
                role_id = row.get("verification_role_id")
                if role_id:
                    self.bot.add_view(VerificationView(role_id=int(role_id)))
                    logger.debug(f"Restored verification view for guild {row.get('guild_id')}")

            if rows:
                logger.info(f"Restored {len(rows)} persistent verification views")

        except Exception as e:
            logger.error(f"Failed to restore verification views: {e}")

    # ─── Commands ─────────────────────────────────────────────────────────────

    verify_group = app_commands.Group(
        name="verify",
        description="Verification system configuration",
    )

    @verify_group.command(name="setup", description="Set up the button verification system")
    @app_commands.describe(
        channel="Channel where the verification message will be posted",
        role="Role to assign to verified members",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def verify_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        role: discord.Role,
    ) -> None:
        if not await self.bot.assert_licensed(interaction):
            return

        # ENTERPRISE tier required for verification
        tier = await self.bot.license_manager.get_tier(interaction.guild.id)
        if tier != "ENTERPRISE":
            await interaction.response.send_message(
                "⚠️ The verification system requires an **ENTERPRISE** license.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        # Check bot can send messages + manage roles
        if not channel.permissions_for(guild.me).send_messages:
            await interaction.followup.send(
                f"❌ I can't send messages in {channel.mention}.", ephemeral=True
            )
            return

        if not guild.me.guild_permissions.manage_roles:
            await interaction.followup.send(
                "❌ I need the **Manage Roles** permission.", ephemeral=True
            )
            return

        if role >= guild.me.top_role:
            await interaction.followup.send(
                f"❌ The **{role.name}** role is above my highest role. "
                "Move my role higher in the role list.", ephemeral=True
            )
            return

        # Save settings
        await self._save_settings(guild.id, channel.id, role.id)

        # Send verification embed + button
        embed = discord.Embed(
            title="✅ Server Verification Required",
            description=(
                "To gain access to this server, you must verify that you are not a bot.\n\n"
                "Click the **Verify Me** button below to get started.\n"
                f"You will receive the **{role.name}** role upon completion."
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text="KLAUD-NINJA Verification System • Powered by AI")

        view = VerificationView(role_id=role.id)
        self.bot.add_view(view)  # Register as persistent

        await channel.send(embed=embed, view=view)

        await interaction.followup.send(
            f"✅ Verification system activated!\n"
            f"• Channel: {channel.mention}\n"
            f"• Role: **{role.name}**",
            ephemeral=True,
        )

        logger.info(
            f"Verification setup | guild={guild.id} | channel={channel.id} | role={role.id}"
        )

    @verify_group.command(name="status", description="Show verification system status")
    async def verify_status(self, interaction: discord.Interaction) -> None:
        if not await self.bot.assert_licensed(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id

        if self.bot.db.is_postgres():
            row = await self.bot.db.fetchrow(
                "SELECT verification_channel_id, verification_role_id FROM guild_settings "
                "WHERE guild_id = $1",
                guild_id,
            )
        elif self.bot.db.is_sqlite():
            row = await self.bot.db.fetchrow(
                None, guild_id,
                sqlite_query=(
                    "SELECT verification_channel_id, verification_role_id "
                    "FROM guild_settings WHERE guild_id = ?"
                ),
            )
        else:
            row = None

        if not row or not row.get("verification_role_id"):
            await interaction.followup.send(
                "ℹ️ Verification is not set up. Use `/verify setup` to configure it.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        channel_id = row.get("verification_channel_id")
        role_id = row.get("verification_role_id")

        channel = guild.get_channel(int(channel_id)) if channel_id else None
        role = guild.get_role(int(role_id)) if role_id else None

        embed = discord.Embed(
            title="🔐 Verification Status",
            color=discord.Color.green(),
        )
        embed.add_field(name="Status", value="🟢 Active", inline=True)
        embed.add_field(
            name="Channel",
            value=channel.mention if channel else "Not found",
            inline=True,
        )
        embed.add_field(
            name="Verified Role",
            value=role.mention if role else "Not found",
            inline=True,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @verify_group.command(name="disable", description="Disable the verification system")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def verify_disable(self, interaction: discord.Interaction) -> None:
        if not await self.bot.assert_licensed(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id

        try:
            if self.bot.db.is_postgres():
                await self.bot.db.execute(
                    "UPDATE guild_settings SET verification_channel_id = NULL, "
                    "verification_role_id = NULL WHERE guild_id = $1",
                    guild_id,
                )
            elif self.bot.db.is_sqlite():
                await self.bot.db.execute(
                    None, guild_id,
                    sqlite_query=(
                        "UPDATE guild_settings "
                        "SET verification_channel_id = NULL, verification_role_id = NULL "
                        "WHERE guild_id = ?"
                    ),
                )
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to disable: {e}", ephemeral=True)
            return

        await interaction.followup.send(
            "✅ Verification system disabled. The button will no longer function.",
            ephemeral=True,
        )

    # ─── DB helpers ───────────────────────────────────────────────────────────

    async def _save_settings(self, guild_id: int, channel_id: int, role_id: int) -> None:
        try:
            if self.bot.db.is_postgres():
                await self.bot.db.execute(
                    """
                    INSERT INTO guild_settings (guild_id, verification_channel_id, verification_role_id)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (guild_id) DO UPDATE
                    SET verification_channel_id = $2, verification_role_id = $3, updated_at = NOW()
                    """,
                    guild_id, channel_id, role_id,
                )
            elif self.bot.db.is_sqlite():
                existing = await self.bot.db.fetchrow(
                    None, guild_id,
                    sqlite_query="SELECT guild_id FROM guild_settings WHERE guild_id = ?",
                )
                if existing:
                    await self.bot.db.execute(
                        None, channel_id, role_id, guild_id,
                        sqlite_query=(
                            "UPDATE guild_settings "
                            "SET verification_channel_id=?, verification_role_id=? "
                            "WHERE guild_id=?"
                        ),
                    )
                else:
                    await self.bot.db.execute(
                        None, guild_id, channel_id, role_id,
                        sqlite_query=(
                            "INSERT INTO guild_settings "
                            "(guild_id, verification_channel_id, verification_role_id) "
                            "VALUES (?,?,?)"
                        ),
                    )
        except Exception as e:
            logger.error(f"Failed to save verify settings: {e}")


async def setup(bot: KlaudBot) -> None:
    await bot.add_cog(SetupVerifyCog(bot))
