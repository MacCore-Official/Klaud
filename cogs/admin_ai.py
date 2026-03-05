"""
KLAUD-NINJA — Admin AI Cog
Allows server admins to control the server by @mentioning Klaud in natural language.
Example: @Klaud create 3 channels in category Trading called GAG SAB LOL

Actions are parsed by Gemini and executed safely with permission validation.
Risky actions require a confirmation button press before executing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.bot import KlaudBot
from services.gemini_service import AdminCommandDecision

logger = logging.getLogger("klaud.admin_ai")

# Actions that require PRO or higher
_PRO_ACTIONS = frozenset({
    "create_category", "delete_channel", "rename_channel", "set_permissions",
    "create_role", "assign_role", "lock_channel", "unlock_channel",
    "setup_basic_server", "bulk_create_channels",
})

# Actions that require ENTERPRISE
_ENTERPRISE_ACTIONS = frozenset({
    "setup_verification",
})


class ConfirmView(discord.ui.View):
    """A simple confirm / cancel button pair for risky AI actions."""

    def __init__(self, timeout: float = 30.0) -> None:
        super().__init__(timeout=timeout)
        self.confirmed: Optional[bool] = None

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


class AdminAICog(commands.Cog, name="AdminAI"):
    """Natural language server management powered by Gemini."""

    def __init__(self, bot: KlaudBot) -> None:
        self.bot = bot

    # ─── Mention listener ─────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Intercept messages that mention the bot and treat them as admin commands."""
        if not message.guild:
            return
        if message.author.bot:
            return
        if self.bot.user not in message.mentions:
            return

        # Strip the mention and whitespace
        content = message.content
        for mention_str in (f"<@{self.bot.user.id}>", f"<@!{self.bot.user.id}>"):
            content = content.replace(mention_str, "")
        content = content.strip()

        if not content:
            return

        # Gate on license
        if not await self.bot.license_manager.is_licensed(message.guild.id):
            await message.reply(self.bot.license_manager.UNLICENSED_MESSAGE, mention_author=False)
            return

        # Gate on admin permissions
        member = message.author
        if not (
            isinstance(member, discord.Member)
            and (member.guild_permissions.administrator or member.guild_permissions.manage_guild)
        ) and member.id != self.bot.settings.BOT_OWNER_ID:
            await message.reply(
                "❌ Only server administrators can give me instructions.",
                mention_author=False,
            )
            return

        # Tier gate on AI admin feature
        tier = await self.bot.license_manager.get_tier(message.guild.id)
        if tier == "BASIC":
            await message.reply(
                "⚠️ AI admin commands require a **PRO** or **ENTERPRISE** license.\n"
                "Use `/license info` to see upgrade options.",
                mention_author=False,
            )
            return

        # Show typing while AI processes
        async with message.channel.typing():
            await self._handle_admin_instruction(message, content, tier)

    async def _handle_admin_instruction(
        self,
        message: discord.Message,
        instruction: str,
        tier: str,
    ) -> None:
        """Parse and execute an admin instruction."""
        guild = message.guild

        # Build context for the AI
        guild_context = (
            f"Server name: {guild.name}\n"
            f"Existing categories: {[c.name for c in guild.categories[:10]]}\n"
            f"Existing channels: {[c.name for c in guild.text_channels[:15]]}\n"
            f"Existing roles: {[r.name for r in guild.roles[:10]]}"
        )

        # Get AI decision
        decision = await self.bot.gemini.parse_admin_command(
            instruction=instruction,
            guild_context=guild_context,
        )

        if not decision.valid:
            await message.reply(
                f"🤔 {decision.explanation or 'I could not understand that instruction. Please be more specific.'}",
                mention_author=False,
            )
            return

        # Tier gate for specific actions
        action = decision.action_type
        if action in _ENTERPRISE_ACTIONS and tier != "ENTERPRISE":
            await message.reply(
                f"⚠️ The `{action}` action requires an **ENTERPRISE** license.",
                mention_author=False,
            )
            return

        # Confirmation for risky actions
        if decision.confirmation_required:
            view = ConfirmView(timeout=30.0)
            confirm_msg = await message.reply(
                f"⚠️ **This action requires confirmation:**\n{decision.explanation}\n\nProceed?",
                view=view,
                mention_author=False,
            )
            await view.wait()

            if not view.confirmed:
                await confirm_msg.edit(
                    content="❌ Action cancelled.",
                    view=None,
                )
                return

            await confirm_msg.edit(view=None)

        # Execute the action
        try:
            result = await self._execute_action(guild, decision, message.author)
            reply = f"✅ {decision.explanation}\n{result}" if result else f"✅ {decision.explanation}"
        except discord.Forbidden:
            reply = "❌ I don't have permission to do that. Check my role permissions."
        except Exception as e:
            logger.error(f"Admin AI action failed: {e}", exc_info=True)
            reply = f"❌ Something went wrong while executing that action: {e}"

        await message.reply(reply, mention_author=False)

        # Audit log
        await self._write_audit(
            guild_id=guild.id,
            user_id=message.author.id,
            action_type=action,
            details=decision.parameters,
            success="❌" not in reply,
            error_msg=reply if "❌" in reply else None,
        )

    # ─── Action executor ──────────────────────────────────────────────────────

    async def _execute_action(
        self,
        guild: discord.Guild,
        decision: AdminCommandDecision,
        requester: discord.Member,
    ) -> str:
        """Route to the appropriate action handler. Returns a result string."""
        action = decision.action_type
        params = decision.parameters

        handlers = {
            "create_category": self._create_category,
            "create_channel": self._create_channel,
            "bulk_create_channels": self._bulk_create_channels,
            "delete_channel": self._delete_channel,
            "rename_channel": self._rename_channel,
            "set_permissions": self._set_permissions,
            "create_role": self._create_role,
            "assign_role": self._assign_role,
            "lock_channel": self._lock_channel,
            "unlock_channel": self._unlock_channel,
            "setup_verification": self._setup_verification,
            "setup_basic_server": self._setup_basic_server,
        }

        handler = handlers.get(action)
        if not handler:
            return f"Unknown action type: `{action}`"

        return await handler(guild, params, requester)

    async def _create_category(
        self, guild: discord.Guild, params: dict, requester: discord.Member
    ) -> str:
        name = params.get("name", "New Category")
        category = await guild.create_category(name)
        return f"Created category **{category.name}**."

    async def _create_channel(
        self, guild: discord.Guild, params: dict, requester: discord.Member
    ) -> str:
        name = params.get("name", "new-channel")
        channel_type = params.get("type", "text").lower()
        category_name = params.get("category")
        topic = params.get("topic")

        category = None
        if category_name:
            category = discord.utils.get(guild.categories, name=category_name)
            if not category:
                category = await guild.create_category(category_name)

        if channel_type == "voice":
            channel = await guild.create_voice_channel(name, category=category)
        else:
            kwargs: dict[str, Any] = {"category": category}
            if topic:
                kwargs["topic"] = topic
            channel = await guild.create_text_channel(name, **kwargs)

        return f"Created {'voice' if channel_type == 'voice' else 'text'} channel {channel.mention}."

    async def _bulk_create_channels(
        self, guild: discord.Guild, params: dict, requester: discord.Member
    ) -> str:
        channels_spec = params.get("channels", [])
        if not channels_spec:
            return "No channels specified."

        created = []
        for spec in channels_spec[:10]:  # Max 10 at once
            name = spec.get("name", "channel")
            category_name = spec.get("category")
            channel_type = spec.get("type", "text").lower()

            category = None
            if category_name:
                category = discord.utils.get(guild.categories, name=category_name)
                if not category:
                    category = await guild.create_category(category_name)
                    await asyncio.sleep(0.5)  # Rate limit friendly

            if channel_type == "voice":
                ch = await guild.create_voice_channel(name, category=category)
            else:
                ch = await guild.create_text_channel(name, category=category)

            created.append(ch.mention)
            await asyncio.sleep(0.3)

        return f"Created channels: {', '.join(created)}."

    async def _delete_channel(
        self, guild: discord.Guild, params: dict, requester: discord.Member
    ) -> str:
        name = params.get("channel_name", "")
        channel = discord.utils.get(guild.channels, name=name)
        if not channel:
            return f"Channel `{name}` not found."
        await channel.delete(reason=f"Deleted by admin AI ({requester})")
        return f"Deleted channel `{name}`."

    async def _rename_channel(
        self, guild: discord.Guild, params: dict, requester: discord.Member
    ) -> str:
        old = params.get("old_name", "")
        new = params.get("new_name", "")
        channel = discord.utils.get(guild.channels, name=old)
        if not channel:
            return f"Channel `{old}` not found."
        await channel.edit(name=new)
        return f"Renamed `{old}` → `{new}`."

    async def _set_permissions(
        self, guild: discord.Guild, params: dict, requester: discord.Member
    ) -> str:
        channel_name = params.get("channel_name", "")
        role_name = params.get("role_name", "")
        allow_perms = params.get("allow", [])
        deny_perms = params.get("deny", [])

        channel = discord.utils.get(guild.channels, name=channel_name)
        role = discord.utils.get(guild.roles, name=role_name)

        if not channel:
            return f"Channel `{channel_name}` not found."
        if not role:
            return f"Role `{role_name}` not found."

        allow_kwargs = {p: True for p in allow_perms if hasattr(discord.Permissions, p)}
        deny_kwargs = {p: False for p in deny_perms if hasattr(discord.Permissions, p)}
        overwrite = discord.PermissionOverwrite(**{**allow_kwargs, **deny_kwargs})

        await channel.set_permissions(role, overwrite=overwrite)
        return f"Permissions updated for **{role.name}** in `{channel_name}`."

    async def _create_role(
        self, guild: discord.Guild, params: dict, requester: discord.Member
    ) -> str:
        name = params.get("name", "New Role")
        color_hex = params.get("color")
        color = discord.Color.default()
        if color_hex:
            try:
                color = discord.Color(int(color_hex.lstrip("#"), 16))
            except (ValueError, AttributeError):
                pass

        role = await guild.create_role(name=name, color=color)
        return f"Created role **{role.name}**."

    async def _assign_role(
        self, guild: discord.Guild, params: dict, requester: discord.Member
    ) -> str:
        role_name = params.get("role_name", "")
        user_mention = params.get("user_mention", "")

        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            return f"Role `{role_name}` not found."

        # Try to find member by mention/ID
        member = None
        if user_mention:
            uid_str = user_mention.strip("<@!>").strip()
            try:
                member = guild.get_member(int(uid_str)) or await guild.fetch_member(int(uid_str))
            except (ValueError, discord.NotFound):
                pass

        if not member:
            return f"Could not find member `{user_mention}`."

        await member.add_roles(role)
        return f"Assigned **{role.name}** to {member.mention}."

    async def _lock_channel(
        self, guild: discord.Guild, params: dict, requester: discord.Member
    ) -> str:
        channel_name = params.get("channel_name", "")
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if not channel:
            return f"Channel `{channel_name}` not found."

        await channel.set_permissions(
            guild.default_role,
            send_messages=False,
            reason=f"Locked by admin AI ({requester})",
        )
        return f"🔒 Locked {channel.mention}. Members can no longer send messages."

    async def _unlock_channel(
        self, guild: discord.Guild, params: dict, requester: discord.Member
    ) -> str:
        channel_name = params.get("channel_name", "")
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if not channel:
            return f"Channel `{channel_name}` not found."

        await channel.set_permissions(
            guild.default_role,
            send_messages=None,
            reason=f"Unlocked by admin AI ({requester})",
        )
        return f"🔓 Unlocked {channel.mention}. Members can send messages again."

    async def _setup_verification(
        self, guild: discord.Guild, params: dict, requester: discord.Member
    ) -> str:
        channel_name = params.get("channel_name", "verify")
        role_name = params.get("role_name", "Verified")

        # Create the role if it doesn't exist
        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            role = await guild.create_role(
                name=role_name,
                color=discord.Color.green(),
                reason="KLAUD verification setup",
            )

        # Create or find the verification channel
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if not channel:
            channel = await guild.create_text_channel(
                channel_name,
                reason="KLAUD verification setup",
            )

        # Save settings to DB
        await self._save_verify_settings(guild.id, channel.id, role.id)

        # Post the verification embed
        embed = discord.Embed(
            title="✅ Server Verification",
            description=(
                "Click the button below to verify you are not a bot "
                "and gain access to the server."
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text="KLAUD-NINJA Verification System")

        from cogs.setup_verify import VerificationView
        await channel.send(embed=embed, view=VerificationView(role_id=role.id))

        return (
            f"Verification system set up! Channel: {channel.mention} | Role: **{role.name}**"
        )

    async def _setup_basic_server(
        self, guild: discord.Guild, params: dict, requester: discord.Member
    ) -> str:
        """Create a standard server layout: categories + channels + roles."""
        created: list[str] = []

        # Standard roles
        roles_to_create = ["Member", "Moderator", "VIP"]
        role_colors = {
            "Member": discord.Color.default(),
            "Moderator": discord.Color.blue(),
            "VIP": discord.Color.gold(),
        }
        for role_name in roles_to_create:
            if not discord.utils.get(guild.roles, name=role_name):
                await guild.create_role(
                    name=role_name,
                    color=role_colors.get(role_name, discord.Color.default()),
                )
                created.append(f"Role: **{role_name}**")
                await asyncio.sleep(0.3)

        # Standard categories + channels
        layout = {
            "📢 Information": ["announcements", "rules", "welcome"],
            "💬 General": ["general", "off-topic", "media"],
            "🛠️ Staff": ["staff-chat", "mod-log"],
        }

        for category_name, channel_names in layout.items():
            cat = discord.utils.get(guild.categories, name=category_name)
            if not cat:
                cat = await guild.create_category(category_name)
                await asyncio.sleep(0.3)

            for ch_name in channel_names:
                if not discord.utils.get(guild.text_channels, name=ch_name):
                    await guild.create_text_channel(ch_name, category=cat)
                    created.append(f"#{ch_name}")
                    await asyncio.sleep(0.3)

        result = "\n".join(f"• {c}" for c in created) if created else "Everything already existed."
        return f"Basic server layout complete:\n{result}"

    async def _save_verify_settings(
        self, guild_id: int, channel_id: int, role_id: int
    ) -> None:
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
            logger.error(f"Failed to save verification settings: {e}")

    async def _write_audit(
        self,
        guild_id: int,
        user_id: int,
        action_type: str,
        details: dict,
        success: bool,
        error_msg: Optional[str] = None,
    ) -> None:
        try:
            if self.bot.db.is_postgres():
                await self.bot.db.execute(
                    """
                    INSERT INTO audit_log (guild_id, user_id, action_type, details, success, error_msg)
                    VALUES ($1, $2, $3, $4::jsonb, $5, $6)
                    """,
                    guild_id, user_id, action_type,
                    json.dumps(details), success, error_msg,
                )
            elif self.bot.db.is_sqlite():
                await self.bot.db.execute(
                    None,
                    guild_id, user_id, action_type,
                    json.dumps(details), int(success), error_msg,
                    sqlite_query=(
                        "INSERT INTO audit_log "
                        "(guild_id, user_id, action_type, details, success, error_msg) "
                        "VALUES (?,?,?,?,?,?)"
                    ),
                )
        except Exception as e:
            logger.error(f"Audit log write failed: {e}")


async def setup(bot: KlaudBot) -> None:
    await bot.add_cog(AdminAICog(bot))
