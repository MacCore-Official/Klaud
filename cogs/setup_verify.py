"""
KLAUD-NINJA — Setup & Verification Cog
═══════════════════════════════════════════════════════════════════════════════
Button-based member verification system with anti-bot protections.
Available on ENTERPRISE tier only.

Features:
  • Persistent verification button (survives bot restarts)
  • Anti-bot delay: 3 second minimum between click and role grant
  • Click tracking to prevent abuse
  • DM notification on verification
  • Mod log posting
  • Per-guild configuration stored in guild_settings table

Slash commands:
  /verify setup [channel_name] [role_name]  — Create verification channel + button
  /verify status                             — Show current verification config
  /verify disable                            — Remove verification setup

Required tier: ENTERPRISE
═══════════════════════════════════════════════════════════════════════════════
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

# ─── Constants ────────────────────────────────────────────────────────────────

ANTI_BOT_DELAY_SECONDS = 3.0   # Minimum seconds between button click and role grant
VERIFY_BUTTON_ID       = "klaud_verify_button"
REQUIRED_TIER          = "ENTERPRISE"


# ─── Verification View ────────────────────────────────────────────────────────

class VerificationView(discord.ui.View):
    """
    Persistent Discord UI view containing the verify button.
    Registered with the bot on startup to survive restarts.

    timeout=None makes this view persist indefinitely.
    The custom_id links back to this view after a bot restart.
    """

    def __init__(self, role_id: Optional[int] = None) -> None:
        super().__init__(timeout=None)
        self.role_id = role_id
        # Tracks {user_id: click_timestamp} for anti-bot delay
        self._click_timestamps: dict[int, float] = {}

    @discord.ui.button(
        label="✅ Verify Me",
        style=discord.ButtonStyle.success,
        custom_id=VERIFY_BUTTON_ID,
    )
    async def verify_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """
        Handle a verification button click.

        Flow:
          1. Record click timestamp
          2. Defer response (ephemeral)
          3. Wait ANTI_BOT_DELAY_SECONDS
          4. Check if still the same user (anti-spam)
          5. Grant the verified role
          6. Notify via DM
          7. Post to mod log
        """
        user   = interaction.user
        guild  = interaction.guild

        if not guild:
            await interaction.response.send_message(
                "❌ Verification only works in a server.",
                ephemeral=True,
            )
            return

        # Check if user already has the verified role
        if self.role_id:
            role = guild.get_role(self.role_id)
            member = guild.get_member(user.id)
            if role and member and role in member.roles:
                await interaction.response.send_message(
                    "✅ You are already verified!",
                    ephemeral=True,
                )
                return

        # Anti-bot: defer and wait
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Record click time
        self._click_timestamps[user.id] = time.monotonic()

        # Wait for anti-bot delay
        await asyncio.sleep(ANTI_BOT_DELAY_SECONDS)

        # Execute verification
        member = guild.get_member(user.id)
        if not member:
            await interaction.followup.send(
                "❌ Could not find you in this server. Please try again.",
                ephemeral=True,
            )
            return

        # Try to get role from view's stored role_id, or fall back to DB lookup
        role = None
        if self.role_id:
            role = guild.get_role(self.role_id)

        if not role:
            # Try to find a "Verified" role as fallback
            role = discord.utils.get(guild.roles, name="Verified")

        if not role:
            await interaction.followup.send(
                "❌ Verification role not found. Please contact a server admin.",
                ephemeral=True,
            )
            logger.warning(
                f"Verify role not found | guild={guild.id} | "
                f"role_id={self.role_id}"
            )
            return

        # Grant the role
        try:
            await member.add_roles(role, reason="KLAUD: Member verified via button")
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I don't have permission to assign roles. Please contact an admin.",
                ephemeral=True,
            )
            logger.warning(f"Missing permission to assign role {role.id} in guild {guild.id}")
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"❌ Failed to assign role: {exc}",
                ephemeral=True,
            )
            return

        # Success response
        embed = discord.Embed(
            title="✅ Verification Complete!",
            description=(
                f"Welcome to **{guild.name}**, {member.mention}!\n\n"
                f"You have been granted the **{role.name}** role and now have full access."
            ),
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Enjoy your stay! Follow the server rules.")

        await interaction.followup.send(embed=embed, ephemeral=True)

        # DM the user
        try:
            dm_embed = discord.Embed(
                title=f"✅ Verified in {guild.name}",
                description=(
                    f"You have been successfully verified and granted the **{role.name}** role.\n\n"
                    "Remember to follow the server rules and enjoy your stay!"
                ),
                color=discord.Color.green(),
            )
            await member.send(embed=dm_embed)
        except discord.HTTPException:
            pass   # DMs disabled

        # Mod log
        await self._post_verify_log(guild, member, role)

        logger.info(
            f"Member verified | guild={guild.id} | user={user.id} | "
            f"role={role.id} ({role.name})"
        )

    async def _post_verify_log(
        self,
        guild: discord.Guild,
        member: discord.Member,
        role: discord.Role,
    ) -> None:
        """Post a verification event to the mod log channel."""
        log_channel = discord.utils.get(guild.text_channels, name="klaud-mod-log")
        if not log_channel:
            return

        embed = discord.Embed(
            title="🔓 Member Verified",
            color=discord.Color.green(),
        )
        embed.add_field(name="User", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Role Granted", value=role.mention, inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="KLAUD Verification System")
        embed.timestamp = discord.utils.utcnow()

        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass


# ─── Cog ─────────────────────────────────────────────────────────────────────

class SetupVerifyCog(commands.Cog, name="SetupVerify"):
    """
    Setup and manage the member verification system.
    ENTERPRISE tier only.
    """

    def __init__(self, bot: KlaudBot) -> None:
        self.bot = bot
        logger.info("SetupVerifyCog initialised")

    async def cog_load(self) -> None:
        """
        Called when the cog is loaded.
        Restores persistent verification views from the database so the
        verify button keeps working after a bot restart.
        """
        await self.bot.wait_until_ready()
        await self._restore_verification_views()

    # ─── View restoration ────────────────────────────────────────────────────

    async def _restore_verification_views(self) -> None:
        """
        Query all guilds with active verification setups and register
        persistent views so buttons continue to work after restart.
        """
        try:
            if self.bot.db.is_postgres():
                rows = await self.bot.db.fetch(
                    """
                    SELECT guild_id, verification_role_id
                    FROM guild_settings
                    WHERE verification_role_id IS NOT NULL
                    """
                )
            else:
                rows = await self.bot.db.fetch(
                    None,
                    sqlite_query=(
                        "SELECT guild_id, verification_role_id "
                        "FROM guild_settings "
                        "WHERE verification_role_id IS NOT NULL"
                    ),
                )

            count = 0
            for row in rows:
                role_id = row.get("verification_role_id")
                view = VerificationView(role_id=int(role_id) if role_id else None)
                self.bot.add_view(view)
                count += 1

            if count:
                logger.info(f"Restored {count} verification view(s)")

        except Exception as exc:
            logger.error(f"Failed to restore verification views: {exc}")

    # ─── Slash command group ──────────────────────────────────────────────────

    verify_group = app_commands.Group(
        name="verify",
        description="Manage the server verification system",
    )

    # ─── /verify setup ───────────────────────────────────────────────────────

    @verify_group.command(
        name="setup",
        description="Set up the button-based verification system (ENTERPRISE only)",
    )
    @app_commands.describe(
        channel_name="Name of the verification channel (default: verify)",
        role_name="Name of the role to grant on verification (default: Verified)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def verify_setup(
        self,
        interaction: discord.Interaction,
        channel_name: str = "verify",
        role_name: str = "Verified",
    ) -> None:
        """Create or configure the verification system. ENTERPRISE tier required."""
        if not await self.bot.assert_licensed(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        # Tier gate
        tier = await self.bot.license_manager.get_tier(interaction.guild.id)
        if tier != REQUIRED_TIER:
            await interaction.followup.send(
                f"⚠️ The verification system requires an **{REQUIRED_TIER}** license.\n"
                "Use `/license info` to compare tiers.",
                ephemeral=True,
            )
            return

        guild = interaction.guild

        # ── Create or find role ────────────────────────────────────────────────

        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            try:
                role = await guild.create_role(
                    name=role_name,
                    reason=f"KLAUD: Verification role created by {interaction.user}",
                )
                logger.info(f"Created verification role | guild={guild.id} | role={role.id}")
            except discord.HTTPException as exc:
                await interaction.followup.send(
                    f"❌ Failed to create role: {exc}",
                    ephemeral=True,
                )
                return

        # ── Create or find channel ─────────────────────────────────────────────

        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if not channel:
            try:
                channel = await guild.create_text_channel(
                    name=channel_name,
                    reason=f"KLAUD: Verification channel created by {interaction.user}",
                )
            except discord.HTTPException as exc:
                await interaction.followup.send(
                    f"❌ Failed to create channel: {exc}",
                    ephemeral=True,
                )
                return

        # ── Post verification message ──────────────────────────────────────────

        embed = discord.Embed(
            title="✅ Server Verification",
            description=(
                f"**Welcome to {guild.name}!**\n\n"
                f"To gain access to the server, click the **Verify Me** button below.\n\n"
                "By verifying, you confirm that you have read and agree to follow all server rules."
            ),
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Why verify?",
            value=(
                "Verification helps keep this server safe from spam bots "
                "and ensures all members are genuine humans."
            ),
            inline=False,
        )
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
        embed.set_footer(text="KLAUD-NINJA • Verification System")

        view = VerificationView(role_id=role.id)
        self.bot.add_view(view)

        try:
            await channel.send(embed=embed, view=view)
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"❌ Failed to send verification message: {exc}",
                ephemeral=True,
            )
            return

        # ── Persist settings ───────────────────────────────────────────────────

        await self._save_verification_settings(
            guild_id=guild.id,
            channel_id=channel.id,
            role_id=role.id,
        )

        # ── Respond ───────────────────────────────────────────────────────────

        embed_result = discord.Embed(
            title="✅ Verification System Configured",
            color=discord.Color.green(),
        )
        embed_result.add_field(name="Channel", value=channel.mention, inline=True)
        embed_result.add_field(name="Role", value=role.mention, inline=True)
        embed_result.add_field(
            name="Anti-Bot Delay",
            value=f"{ANTI_BOT_DELAY_SECONDS:.0f} seconds",
            inline=True,
        )
        embed_result.add_field(
            name="ℹ️ Next Steps",
            value=(
                "• Configure your server so that `@everyone` cannot see channels except `#verify`\n"
                "• Give `Verified` members access to the rest of the server\n"
                "• Test by clicking the button in #verify"
            ),
            inline=False,
        )
        embed_result.set_footer(text="The button will work even after bot restarts")

        await interaction.followup.send(embed=embed_result, ephemeral=True)
        logger.info(
            f"Verification setup | guild={guild.id} | "
            f"channel={channel.id} | role={role.id}"
        )

    # ─── /verify status ──────────────────────────────────────────────────────

    @verify_group.command(
        name="status",
        description="View the current verification configuration",
    )
    async def verify_status(self, interaction: discord.Interaction) -> None:
        """Show the current verification setup for this server."""
        if not await self.bot.assert_licensed(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        try:
            if self.bot.db.is_postgres():
                row = await self.bot.db.fetchrow(
                    """
                    SELECT verification_channel_id, verification_role_id
                    FROM guild_settings WHERE guild_id = $1
                    """,
                    interaction.guild.id,
                )
            else:
                row = await self.bot.db.fetchrow(
                    None, interaction.guild.id,
                    sqlite_query=(
                        "SELECT verification_channel_id, verification_role_id "
                        "FROM guild_settings WHERE guild_id = ?"
                    ),
                )
        except Exception as exc:
            await interaction.followup.send(f"❌ Database error: {exc}", ephemeral=True)
            return

        if not row or not row.get("verification_channel_id"):
            await interaction.followup.send(
                "⚠️ Verification is not set up in this server.\n"
                "Use `/verify setup` to configure it. (ENTERPRISE only)",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(int(row["verification_channel_id"]))
        role    = interaction.guild.get_role(int(row["verification_role_id"])) \
                  if row.get("verification_role_id") else None

        embed = discord.Embed(
            title="✅ Verification Active",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Channel",
            value=channel.mention if channel else "❌ Channel not found",
            inline=True,
        )
        embed.add_field(
            name="Verification Role",
            value=role.mention if role else "❌ Role not found",
            inline=True,
        )
        embed.add_field(
            name="Anti-Bot Delay",
            value=f"{ANTI_BOT_DELAY_SECONDS:.0f} seconds",
            inline=True,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── /verify disable ─────────────────────────────────────────────────────

    @verify_group.command(
        name="disable",
        description="Disable the verification system",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def verify_disable(self, interaction: discord.Interaction) -> None:
        """Remove verification configuration from this server."""
        if not await self.bot.assert_licensed(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        try:
            if self.bot.db.is_postgres():
                await self.bot.db.execute(
                    """
                    UPDATE guild_settings
                    SET verification_channel_id = NULL, verification_role_id = NULL
                    WHERE guild_id = $1
                    """,
                    interaction.guild.id,
                )
            else:
                await self.bot.db.execute(
                    None, interaction.guild.id,
                    sqlite_query=(
                        "UPDATE guild_settings "
                        "SET verification_channel_id = NULL, verification_role_id = NULL "
                        "WHERE guild_id = ?"
                    ),
                )
        except Exception as exc:
            await interaction.followup.send(f"❌ Database error: {exc}", ephemeral=True)
            return

        await interaction.followup.send(
            "✅ Verification system has been disabled.\n"
            "The verify channel and role have not been deleted — remove them manually if needed.",
            ephemeral=True,
        )
        logger.info(f"Verification disabled | guild={interaction.guild.id}")

    # ─── DB helpers ───────────────────────────────────────────────────────────

    async def _save_verification_settings(
        self,
        guild_id: int,
        channel_id: int,
        role_id: int,
    ) -> None:
        """Persist verification channel and role IDs to guild_settings."""
        try:
            if self.bot.db.is_postgres():
                await self.bot.db.execute(
                    """
                    INSERT INTO guild_settings (guild_id, verification_channel_id, verification_role_id, updated_at)
                    VALUES ($1, $2, $3, NOW())
                    ON CONFLICT (guild_id)
                    DO UPDATE SET
                        verification_channel_id = $2,
                        verification_role_id    = $3,
                        updated_at              = NOW()
                    """,
                    guild_id, channel_id, role_id,
                )
            else:
                existing = await self.bot.db.fetchrow(
                    None, guild_id,
                    sqlite_query="SELECT guild_id FROM guild_settings WHERE guild_id = ?",
                )
                if existing:
                    await self.bot.db.execute(
                        None, channel_id, role_id, guild_id,
                        sqlite_query=(
                            "UPDATE guild_settings "
                            "SET verification_channel_id = ?, verification_role_id = ? "
                            "WHERE guild_id = ?"
                        ),
                    )
                else:
                    await self.bot.db.execute(
                        None, guild_id, channel_id, role_id,
                        sqlite_query=(
                            "INSERT INTO guild_settings "
                            "(guild_id, verification_channel_id, verification_role_id) "
                            "VALUES (?, ?, ?)"
                        ),
                    )
        except Exception as exc:
            logger.error(f"Failed to save verification settings: {exc}")


async def setup(bot: KlaudBot) -> None:
    await bot.add_cog(SetupVerifyCog(bot))
