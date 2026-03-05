"""
KLAUD-NINJA — Admin AI Cog
═══════════════════════════════════════════════════════════════════════════════
Natural language server management via @mentioning the bot.
Available to PRO and ENTERPRISE tier servers only.

Usage:
  @Klaud-Ninja create a general chat channel
  @Klaud-Ninja set up a verification system
  @Klaud-Ninja lock the #announcements channel
  @Klaud-Ninja create roles for Staff, Moderator, and VIP

Supported actions:
  create_category      — Create a channel category
  create_channel       — Create a text or voice channel
  bulk_create_channels — Create multiple channels at once (max 10)
  delete_channel       — Delete a channel
  rename_channel       — Rename a channel
  set_permissions      — Change channel permissions for a role
  create_role          — Create a new role
  assign_role          — Assign a role to a user
  lock_channel         — Deny @everyone from sending messages
  unlock_channel       — Re-allow @everyone to send messages
  setup_verification   — Set up a button-based verification channel
  setup_basic_server   — Bootstrap a standard server structure

Risky actions (delete, set_permissions, setup_basic_server) require
a confirmation button before executing.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import discord
from discord.ext import commands

from core.bot import KlaudBot
from services.groq_service import AdminCommandDecision

logger = logging.getLogger("klaud.admin_ai")

# ─── Tier gate ────────────────────────────────────────────────────────────────

_REQUIRED_TIERS = frozenset({"PRO", "ENTERPRISE"})
_ENTERPRISE_ACTIONS = frozenset({"setup_verification"})

# Actions that need an explicit confirm button
_RISKY_ACTIONS = frozenset({
    "delete_channel",
    "delete_category",
    "set_permissions",
    "setup_basic_server",
    "bulk_create_channels",
})


# ─── Confirmation View ───────────────────────────────────────────────────────

class ConfirmView(discord.ui.View):
    """
    Confirmation prompt with Confirm / Cancel buttons.
    Used before executing destructive or large-scale actions.
    Times out after 30 seconds.
    """

    def __init__(self) -> None:
        super().__init__(timeout=30.0)
        self.confirmed: Optional[bool] = None

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success)
    async def confirm_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.confirmed = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.confirmed = False
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self) -> None:
        self.confirmed = False
        self.stop()


# ─── Cog ─────────────────────────────────────────────────────────────────────

class AdminAICog(commands.Cog, name="AdminAI"):
    """
    Natural language Discord server management powered by Groq.
    Triggered when an admin @mentions the bot with a command.
    """

    def __init__(self, bot: KlaudBot) -> None:
        self.bot = bot
        logger.info("AdminAICog initialised")

    # ─── Message listener ─────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        Listen for @mentions of the bot.
        Only processes messages where the bot is mentioned and the author is
        an admin with Manage Guild permission.
        """
        if message.author.bot:
            return

        if not message.guild:
            return

        # Must mention the bot
        if self.bot.user not in message.mentions:
            return

        # Must have admin permission
        member = message.guild.get_member(message.author.id)
        if not member or not member.guild_permissions.manage_guild:
            return

        # License gate
        if not await self.bot.license_manager.is_licensed(message.guild.id):
            return

        # Tier gate — PRO or ENTERPRISE only
        tier = await self.bot.license_manager.get_tier(message.guild.id)
        if tier not in _REQUIRED_TIERS:
            await message.reply(
                "⚠️ AI admin commands require a **PRO** or **ENTERPRISE** license.\n"
                "Use `/license info` to compare tiers.",
                mention_author=False,
            )
            return

        # Extract the instruction (remove the mention)
        content = message.content
        for mention_str in [f"<@{self.bot.user.id}>", f"<@!{self.bot.user.id}>"]:
            content = content.replace(mention_str, "").strip()

        if not content:
            await message.reply(
                "👋 What would you like me to do?\n"
                "Example: `create a #general channel`, `set up verification`, `lock #announcements`",
                mention_author=False,
            )
            return

        # Typing indicator while AI processes
        async with message.channel.typing():
            await self._process_admin_command(message, content, tier)

    # ─── Command processing ───────────────────────────────────────────────────

    async def _process_admin_command(
        self,
        message: discord.Message,
        instruction: str,
        tier: str,
    ) -> None:
        """Parse and execute a natural language admin instruction."""

        # Build guild context for the AI
        guild = message.guild
        guild_context = (
            f"Guild name: {guild.name}\n"
            f"Member count: {guild.member_count}\n"
            f"Text channels: {[c.name for c in guild.text_channels[:10]]}\n"
            f"Roles: {[r.name for r in guild.roles[:10]]}"
        )

        try:
            decision = await asyncio.wait_for(
                self.bot.groq.parse_admin_command(
                    instruction=instruction,
                    guild_context=guild_context,
                ),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            await message.reply(
                "⏱️ The AI took too long to respond. Please try again.",
                mention_author=False,
            )
            return
        except Exception as exc:
            logger.error(f"Admin AI error: {exc}", exc_info=True)
            await message.reply(
                "❌ An error occurred while processing your request. Please try again.",
                mention_author=False,
            )
            return

        if not decision.valid:
            await message.reply(
                f"🤔 {decision.explanation or 'I could not understand that instruction. Please be more specific.'}",
                mention_author=False,
            )
            return

        # Enterprise-only action check
        if decision.action_type in _ENTERPRISE_ACTIONS and tier != "ENTERPRISE":
            await message.reply(
                f"⚠️ **{decision.action_type}** requires an **ENTERPRISE** license.",
                mention_author=False,
            )
            return

        # Risky action — require confirmation
        if decision.action_type in _RISKY_ACTIONS or decision.confirmation_required:
            confirmed = await self._request_confirmation(message, decision)
            if not confirmed:
                await message.reply("❌ Action cancelled.", mention_author=False)
                return

        # Execute the action
        await self._execute_action(message, decision)

    # ─── Confirmation ────────────────────────────────────────────────────────

    async def _request_confirmation(
        self,
        message: discord.Message,
        decision: AdminCommandDecision,
    ) -> bool:
        """Send a confirmation prompt and wait for the user's response."""
        embed = discord.Embed(
            title="⚠️ Confirm Action",
            description=(
                f"**Action:** `{decision.action_type}`\n\n"
                f"**What will happen:**\n{decision.explanation}\n\n"
                "This action may be difficult to undo. Continue?"
            ),
            color=discord.Color.orange(),
        )

        view = ConfirmView()
        reply = await message.reply(embed=embed, view=view, mention_author=False)

        await view.wait()

        try:
            await reply.edit(view=view)
        except discord.HTTPException:
            pass

        return view.confirmed is True

    # ─── Action router ────────────────────────────────────────────────────────

    async def _execute_action(
        self,
        message: discord.Message,
        decision: AdminCommandDecision,
    ) -> None:
        """Route the parsed decision to the appropriate handler."""
        guild   = message.guild
        action  = decision.action_type
        params  = decision.parameters

        handlers = {
            "create_category":      self._create_category,
            "create_channel":       self._create_channel,
            "bulk_create_channels": self._bulk_create_channels,
            "delete_channel":       self._delete_channel,
            "rename_channel":       self._rename_channel,
            "set_permissions":      self._set_permissions,
            "create_role":          self._create_role,
            "assign_role":          self._assign_role,
            "lock_channel":         self._lock_channel,
            "unlock_channel":       self._unlock_channel,
            "setup_verification":   self._setup_verification,
            "setup_basic_server":   self._setup_basic_server,
        }

        handler = handlers.get(action)
        if not handler:
            await message.reply(
                f"🤔 I don't know how to perform `{action}` yet.",
                mention_author=False,
            )
            return

        try:
            await handler(message, guild, params)
            await self._log_audit(guild, message.author.id, action, params, success=True)
        except discord.Forbidden:
            await message.reply(
                "❌ I don't have permission to do that. "
                "Please ensure I have the required permissions.",
                mention_author=False,
            )
            await self._log_audit(guild, message.author.id, action, params,
                                  success=False, error="Missing permissions")
        except Exception as exc:
            logger.error(f"Action execution error ({action}): {exc}", exc_info=True)
            await message.reply(
                f"❌ Failed to execute `{action}`: {exc}",
                mention_author=False,
            )
            await self._log_audit(guild, message.author.id, action, params,
                                  success=False, error=str(exc))

    # ─── Action handlers ──────────────────────────────────────────────────────

    async def _create_category(
        self,
        message: discord.Message,
        guild: discord.Guild,
        params: dict,
    ) -> None:
        name = params.get("name", "New Category")
        category = await guild.create_category(name)
        await message.reply(
            f"✅ Created category **{category.name}**",
            mention_author=False,
        )

    async def _create_channel(
        self,
        message: discord.Message,
        guild: discord.Guild,
        params: dict,
    ) -> None:
        name         = params.get("name", "new-channel")
        cat_name     = params.get("category")
        channel_type = params.get("type", "text").lower()
        topic        = params.get("topic", "")

        category = None
        if cat_name:
            category = discord.utils.get(guild.categories, name=cat_name)

        if channel_type == "voice":
            channel = await guild.create_voice_channel(name=name, category=category)
        else:
            channel = await guild.create_text_channel(
                name=name,
                category=category,
                topic=topic[:1024] if topic else None,
            )

        await message.reply(
            f"✅ Created {channel_type} channel {channel.mention}",
            mention_author=False,
        )

    async def _bulk_create_channels(
        self,
        message: discord.Message,
        guild: discord.Guild,
        params: dict,
    ) -> None:
        channels_to_create = params.get("channels", [])

        if not channels_to_create:
            await message.reply("❌ No channels specified.", mention_author=False)
            return

        # Cap at 10 to prevent abuse
        channels_to_create = channels_to_create[:10]

        created = []
        failed  = []

        for ch in channels_to_create:
            try:
                name     = ch.get("name", "new-channel")
                cat_name = ch.get("category")
                ch_type  = ch.get("type", "text").lower()

                category = None
                if cat_name:
                    category = discord.utils.get(guild.categories, name=cat_name)

                if ch_type == "voice":
                    channel = await guild.create_voice_channel(name=name, category=category)
                else:
                    channel = await guild.create_text_channel(name=name, category=category)

                created.append(channel.mention)
                await asyncio.sleep(0.5)   # Rate limit safety

            except Exception as exc:
                failed.append(f"{ch.get('name', '?')} ({exc})")

        lines = [f"✅ Created {len(created)} channel(s):"]
        lines.extend(created)
        if failed:
            lines.append(f"\n❌ Failed ({len(failed)}):")
            lines.extend(failed)

        await message.reply("\n".join(lines), mention_author=False)

    async def _delete_channel(
        self,
        message: discord.Message,
        guild: discord.Guild,
        params: dict,
    ) -> None:
        channel_name = params.get("channel_name", "")
        channel = discord.utils.get(guild.text_channels, name=channel_name) or \
                  discord.utils.get(guild.voice_channels, name=channel_name)

        if not channel:
            await message.reply(
                f"❌ Channel `{channel_name}` not found.",
                mention_author=False,
            )
            return

        name = channel.name
        await channel.delete(reason=f"Deleted by KLAUD AI on request of {message.author}")
        await message.reply(f"🗑️ Deleted channel `#{name}`", mention_author=False)

    async def _rename_channel(
        self,
        message: discord.Message,
        guild: discord.Guild,
        params: dict,
    ) -> None:
        old_name = params.get("old_name", "")
        new_name = params.get("new_name", "")

        channel = discord.utils.get(guild.text_channels, name=old_name) or \
                  discord.utils.get(guild.voice_channels, name=old_name)

        if not channel:
            await message.reply(
                f"❌ Channel `{old_name}` not found.",
                mention_author=False,
            )
            return

        await channel.edit(name=new_name)
        await message.reply(
            f"✅ Renamed `#{old_name}` → `#{new_name}`",
            mention_author=False,
        )

    async def _set_permissions(
        self,
        message: discord.Message,
        guild: discord.Guild,
        params: dict,
    ) -> None:
        channel_name = params.get("channel_name", "")
        role_name    = params.get("role_name", "")
        allow_perms  = params.get("allow", [])
        deny_perms   = params.get("deny", [])

        channel = discord.utils.get(guild.text_channels, name=channel_name)
        role    = discord.utils.get(guild.roles, name=role_name)

        if not channel:
            await message.reply(f"❌ Channel `{channel_name}` not found.", mention_author=False)
            return

        if not role:
            await message.reply(f"❌ Role `{role_name}` not found.", mention_author=False)
            return

        overwrite = discord.PermissionOverwrite()
        for perm in allow_perms:
            try:
                setattr(overwrite, perm, True)
            except AttributeError:
                pass
        for perm in deny_perms:
            try:
                setattr(overwrite, perm, False)
            except AttributeError:
                pass

        await channel.set_permissions(role, overwrite=overwrite)
        await message.reply(
            f"✅ Updated permissions for **{role.name}** in {channel.mention}",
            mention_author=False,
        )

    async def _create_role(
        self,
        message: discord.Message,
        guild: discord.Guild,
        params: dict,
    ) -> None:
        name  = params.get("name", "New Role")
        color_hex = params.get("color", "")

        color = discord.Color.default()
        if color_hex:
            try:
                color = discord.Color(int(color_hex.lstrip("#"), 16))
            except (ValueError, AttributeError):
                pass

        role = await guild.create_role(name=name, color=color)
        await message.reply(f"✅ Created role **{role.name}**", mention_author=False)

    async def _assign_role(
        self,
        message: discord.Message,
        guild: discord.Guild,
        params: dict,
    ) -> None:
        role_name    = params.get("role_name", "")
        user_mention = params.get("user_mention", "")

        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            await message.reply(f"❌ Role `{role_name}` not found.", mention_author=False)
            return

        # Parse user from mention or ID
        member = None
        if user_mention:
            uid = user_mention.strip("<@!>").strip()
            try:
                member = guild.get_member(int(uid))
            except ValueError:
                member = discord.utils.get(guild.members, name=uid)

        if not member:
            await message.reply("❌ User not found.", mention_author=False)
            return

        await member.add_roles(role, reason=f"Assigned by KLAUD AI")
        await message.reply(
            f"✅ Assigned **{role.name}** to {member.mention}",
            mention_author=False,
        )

    async def _lock_channel(
        self,
        message: discord.Message,
        guild: discord.Guild,
        params: dict,
    ) -> None:
        channel_name = params.get("channel_name", "")
        channel = discord.utils.get(guild.text_channels, name=channel_name)

        if not channel:
            await message.reply(f"❌ Channel `{channel_name}` not found.", mention_author=False)
            return

        overwrite = channel.overwrites_for(guild.default_role)
        overwrite.send_messages = False
        await channel.set_permissions(guild.default_role, overwrite=overwrite)
        await channel.send("🔒 This channel has been locked by a moderator.")
        await message.reply(f"🔒 Locked {channel.mention}", mention_author=False)

    async def _unlock_channel(
        self,
        message: discord.Message,
        guild: discord.Guild,
        params: dict,
    ) -> None:
        channel_name = params.get("channel_name", "")
        channel = discord.utils.get(guild.text_channels, name=channel_name)

        if not channel:
            await message.reply(f"❌ Channel `{channel_name}` not found.", mention_author=False)
            return

        overwrite = channel.overwrites_for(guild.default_role)
        overwrite.send_messages = None
        await channel.set_permissions(guild.default_role, overwrite=overwrite)
        await channel.send("🔓 This channel has been unlocked.")
        await message.reply(f"🔓 Unlocked {channel.mention}", mention_author=False)

    async def _setup_verification(
        self,
        message: discord.Message,
        guild: discord.Guild,
        params: dict,
    ) -> None:
        channel_name = params.get("channel_name", "verify")
        role_name    = params.get("role_name", "Verified")

        # Create or find role
        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            role = await guild.create_role(name=role_name, reason="KLAUD verification setup")

        # Create or find channel
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if not channel:
            channel = await guild.create_text_channel(
                name=channel_name,
                reason="KLAUD verification setup",
            )

        # Post verification embed
        embed = discord.Embed(
            title="✅ Server Verification",
            description=(
                "Click the button below to verify yourself and gain access to the server.\n\n"
                "By verifying, you agree to follow all server rules."
            ),
            color=discord.Color.green(),
        )

        # Import and use the verify view from setup_verify cog
        try:
            verify_cog = self.bot.cogs.get("SetupVerify")
            if verify_cog:
                await channel.send(embed=embed, view=verify_cog.VerificationView(role_id=role.id))
            else:
                await channel.send(embed=embed)
        except Exception:
            await channel.send(embed=embed)

        await message.reply(
            f"✅ Verification set up in {channel.mention} with role **{role.name}**",
            mention_author=False,
        )

    async def _setup_basic_server(
        self,
        message: discord.Message,
        guild: discord.Guild,
        params: dict,
    ) -> None:
        """Bootstrap a standard server structure with categories, channels, and roles."""
        status_msg = await message.reply(
            "⚙️ Setting up basic server structure...",
            mention_author=False,
        )

        created = []

        # Create roles
        role_names = ["Member", "Moderator", "VIP", "Staff"]
        for rname in role_names:
            if not discord.utils.get(guild.roles, name=rname):
                try:
                    await guild.create_role(name=rname)
                    created.append(f"Role: {rname}")
                    await asyncio.sleep(0.5)
                except discord.HTTPException:
                    pass

        # Create categories and channels
        structure = {
            "📋 Information": ["rules", "announcements", "roles"],
            "💬 General":     ["general", "off-topic", "bot-commands"],
            "🛡️ Staff":        ["staff-chat", "mod-log"],
        }

        for cat_name, channels in structure.items():
            try:
                cat = discord.utils.get(guild.categories, name=cat_name)
                if not cat:
                    cat = await guild.create_category(cat_name)
                    created.append(f"Category: {cat_name}")
                    await asyncio.sleep(0.3)

                for ch_name in channels:
                    if not discord.utils.get(guild.text_channels, name=ch_name):
                        await guild.create_text_channel(ch_name, category=cat)
                        created.append(f"Channel: #{ch_name}")
                        await asyncio.sleep(0.5)
            except discord.HTTPException as exc:
                logger.warning(f"Setup error for {cat_name}: {exc}")

        summary = "\n".join(f"  ✅ {item}" for item in created)
        await status_msg.edit(
            content=f"✅ Basic server structure created:\n{summary or '(nothing new needed)'}"
        )

    # ─── Audit logging ────────────────────────────────────────────────────────

    async def _log_audit(
        self,
        guild: discord.Guild,
        user_id: int,
        action_type: str,
        details: dict,
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
        """Persist admin AI action to the audit_log table."""
        try:
            details_str = json.dumps(details)[:2000] if details else "{}"
            if self.bot.db.is_postgres():
                await self.bot.db.execute(
                    """
                    INSERT INTO audit_log (guild_id, user_id, action_type, details, success, error_msg)
                    VALUES ($1, $2, $3, $4::jsonb, $5, $6)
                    """,
                    guild.id, user_id, action_type, details_str, success, error,
                )
            else:
                await self.bot.db.execute(
                    None,
                    guild.id, user_id, action_type, details_str,
                    1 if success else 0, error,
                    sqlite_query=(
                        "INSERT INTO audit_log "
                        "(guild_id, user_id, action_type, details, success, error_msg) "
                        "VALUES (?,?,?,?,?,?)"
                    ),
                )
        except Exception as exc:
            logger.error(f"Audit log error: {exc}")


async def setup(bot: KlaudBot) -> None:
    await bot.add_cog(AdminAICog(bot))
