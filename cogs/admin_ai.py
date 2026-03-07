"""
KLAUD-NINJA — Admin AI Cog
Natural language server management + conversational AI.
@mention the bot to chat OR issue commands.
PRO and ENTERPRISE tier only.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Optional

import discord
from discord.ext import commands

from core.bot import KlaudBot
from services.groq_service import AdminCommandDecision

logger = logging.getLogger("klaud.admin_ai")

_REQUIRED_TIERS = frozenset({"PRO", "ENTERPRISE"})

# Only these two need confirmation — everything else executes immediately
_CONFIRM_ACTIONS = frozenset({"delete_all_channels", "setup_basic_server"})

_API_DELAY = 0.5


# ─── Confirmation View ────────────────────────────────────────────────────────

class ConfirmView(discord.ui.View):
    def __init__(self, author_id: int) -> None:
        super().__init__(timeout=30.0)
        self.author_id = author_id
        self.confirmed: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the invoking admin can confirm.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = True
        for c in self.children: c.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = False
        for c in self.children: c.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self) -> None:
        self.confirmed = False
        self.stop()


# ─── Cog ─────────────────────────────────────────────────────────────────────

class AdminAICog(commands.Cog, name="AdminAI"):
    def __init__(self, bot: KlaudBot) -> None:
        self.bot = bot
        logger.info("AdminAICog initialised")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        if self.bot.user not in message.mentions:
            return

        member = message.guild.get_member(message.author.id)
        if not member or not member.guild_permissions.manage_guild:
            return

        if not await self.bot.license_manager.is_licensed(message.guild.id):
            return

        tier = await self.bot.license_manager.get_tier(message.guild.id)
        if tier not in _REQUIRED_TIERS:
            await _reply(message,
                "⚠️ AI admin commands require **PRO** or **ENTERPRISE** license.\n"
                "Use `/license info` to compare tiers.")
            return

        # Strip mention
        content = message.content
        for token in [f"<@{self.bot.user.id}>", f"<@!{self.bot.user.id}>"]:
            content = content.replace(token, "").strip()

        if not content:
            await self._send_help(message)
            return

        async with message.channel.typing():
            await self._process(message, content, tier)

    # ─── Core pipeline ────────────────────────────────────────────────────────

    async def _process(self, message: discord.Message, instruction: str, tier: str) -> None:
        guild = message.guild
        guild_context = (
            f"Guild: {guild.name} ({guild.member_count} members)\n"
            f"Categories: {[c.name for c in guild.categories[:15]]}\n"
            f"Text channels: {[c.name for c in guild.text_channels[:20]]}\n"
            f"Voice channels: {[c.name for c in guild.voice_channels[:10]]}\n"
            f"Roles: {[r.name for r in guild.roles[:20] if not r.is_default()]}\n"
            f"Bot's highest role: {guild.me.top_role.name}\n"
            f"Current channel: {message.channel.name}"
        )

        try:
            decision = await asyncio.wait_for(
                self.bot.groq.parse_admin_command(instruction, guild_context),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            await _reply(message, "⏱️ AI took too long. Try again.")
            return
        except Exception as exc:
            logger.error(f"Admin AI error: {exc}", exc_info=True)
            await _reply(message, "❌ Error processing your request.")
            return

        if not decision.valid or decision.action_type == "unknown":
            await _reply(message, f"🤔 {decision.explanation or 'Could not understand that.'}")
            return

        # Chat response — just reply naturally
        if decision.action_type == "chat":
            chat_msg = decision.parameters.get("message", "") if isinstance(decision.parameters, dict) else ""
            if not chat_msg:
                chat_msg = decision.explanation or "Hey! How can I help?"
            await _reply(message, chat_msg)
            return

        # Confirmation only for truly destructive actions
        needs_confirm = decision.action_type in _CONFIRM_ACTIONS
        if needs_confirm:
            confirmed = await self._confirm(message, decision)
            if not confirmed:
                await _reply(message, "❌ Cancelled.")
                return

        await self._execute(message, decision)

    # ─── Confirmation ─────────────────────────────────────────────────────────

    async def _confirm(self, message: discord.Message, decision: AdminCommandDecision) -> bool:
        embed = discord.Embed(
            title="⚠️ Are you sure?",
            description=f"**{decision.explanation}**\n\nThis cannot be undone easily.",
            color=discord.Color.orange(),
        )
        view  = ConfirmView(author_id=message.author.id)
        reply = await _reply(message, embed=embed, view=view)
        await view.wait()
        if reply and hasattr(reply, "edit"):
            try: await reply.edit(view=view)
            except Exception: pass
        return view.confirmed is True

    # ─── Router ───────────────────────────────────────────────────────────────

    async def _execute(self, message: discord.Message, decision: AdminCommandDecision) -> None:
        action = decision.action_type
        params = decision.parameters
        guild  = message.guild

        router = {
            "create_category":       self._create_category,
            "create_channel":        self._create_channel,
            "bulk_create_channels":  self._bulk_create_channels,
            "delete_channel":        self._delete_channel,
            "delete_all_channels":   self._delete_all_channels,
            "delete_category":       self._delete_category,
            "rename_channel":        self._rename_channel,
            "lock_channel":          self._lock_channel,
            "unlock_channel":        self._unlock_channel,
            "set_channel_permissions": self._set_channel_permissions,
            "set_permissions":       self._set_channel_permissions,  # legacy alias
            "create_role":           self._create_role,
            "bulk_create_roles":     self._bulk_create_roles,
            "delete_role":           self._delete_role,
            "edit_role_permissions": self._edit_role_permissions,
            "move_role_to_top":      self._move_role_to_top,
            "assign_role":           self._assign_role,
            "remove_role":           self._remove_role,
            "purge_messages":        self._purge_messages,
            "kick_user":             self._kick_user,
            "ban_user":              self._ban_user,
            "unban_user":            self._unban_user,
            "timeout_user":          self._timeout_user,
            "untimeout_user":        self._untimeout_user,
            "setup_verification":    self._setup_verification,
            "setup_basic_server":    self._setup_basic_server,
            "multi_action":          self._multi_action,
        }

        handler = router.get(action)
        if not handler:
            await _reply(message, f"🤔 I don't know how to do `{action}` yet.")
            return

        try:
            await handler(message, guild, params)
            await self._log_audit(guild, message.author.id, action, params, success=True)
        except discord.Forbidden as e:
            err = str(e).lower()
            if "hierarchy" in err or "above" in err:
                await _reply(message,
                    "❌ I can't do that — the target's role is above mine.\n"
                    "Go to **Server Settings → Roles** and drag the **KLAUD-NINJA** role to the top.")
            else:
                await _reply(message,
                    "❌ Missing permissions. Make sure I have the right permissions enabled.")
            logger.warning(f"Forbidden on {action}: {e}")
        except Exception as exc:
            logger.error(f"Action error ({action}): {exc}", exc_info=True)
            await _reply(message, f"❌ Failed: {exc}")

    # ─── Multi-action ──────────────────────────────────────────────────────────

    async def _multi_action(self, message, guild: discord.Guild, params: dict) -> None:
        actions = params.get("actions", [])
        if not actions:
            await _reply(message, "❌ No actions in plan.")
            return
        status  = await _reply(message, f"⚙️ Executing {len(actions)} action(s)...")
        results = []
        router  = {
            "create_category":       self._create_category,
            "create_channel":        self._create_channel,
            "bulk_create_channels":  self._bulk_create_channels,
            "delete_channel":        self._delete_channel,
            "delete_all_channels":   self._delete_all_channels,
            "delete_category":       self._delete_category,
            "rename_channel":        self._rename_channel,
            "create_role":           self._create_role,
            "bulk_create_roles":     self._bulk_create_roles,
            "edit_role_permissions": self._edit_role_permissions,
            "move_role_to_top":      self._move_role_to_top,
            "lock_channel":          self._lock_channel,
            "unlock_channel":        self._unlock_channel,
            "set_channel_permissions": self._set_channel_permissions,
            "set_permissions":       self._set_channel_permissions,
        }
        for step in actions:
            at = step.get("action_type", "")
            sp = step.get("parameters", {})
            h  = router.get(at)
            if h:
                try:
                    col = _ResultCollector()
                    await h(col, guild, sp)
                    results.append(f"✅ {at}: {col.last or 'done'}")
                except discord.Forbidden:
                    results.append(f"❌ {at}: missing permissions (check role hierarchy)")
                except Exception as exc:
                    results.append(f"❌ {at}: {exc}")
            else:
                results.append(f"⚠️ Unknown: {at}")
            await asyncio.sleep(_API_DELAY)

        result = f"✅ **Done!**\n" + "\n".join(results)
        if status and hasattr(status, "edit"):
            try: await status.edit(content=result); return
            except Exception: pass
        await _reply(message, result)

    # ─── Channel handlers ──────────────────────────────────────────────────────

    async def _create_category(self, message, guild: discord.Guild, params: dict) -> None:
        name = params.get("name", "New Category")
        if discord.utils.get(guild.categories, name=name):
            await _reply(message, f"⏭️ Category **{name}** already exists.")
            return
        cat = await guild.create_category(name)
        await _reply(message, f"✅ Created category **{cat.name}**")

    async def _create_channel(self, message, guild: discord.Guild, params: dict) -> None:
        name     = str(params.get("name", "new-channel")).lower().replace(" ", "-")
        cat_name = params.get("category")
        ch_type  = str(params.get("type", "text")).lower()
        topic    = str(params.get("topic", ""))
        category = None
        if cat_name:
            category = discord.utils.get(guild.categories, name=cat_name)
            if not category:
                category = await guild.create_category(cat_name)
        if ch_type == "voice":
            ch = await guild.create_voice_channel(name=name, category=category)
            await _reply(message, f"✅ Created voice channel **{ch.name}**")
        else:
            ch = await guild.create_text_channel(name=name, category=category, topic=topic[:1024] or None)
            await _reply(message, f"✅ Created channel {ch.mention}")

    async def _bulk_create_channels(self, message, guild: discord.Guild, params: dict) -> None:
        channels = params.get("channels", [])[:15]
        if not channels:
            await _reply(message, "❌ No channels specified.")
            return
        created, skipped, failed = [], [], []
        for spec in channels:
            name     = str(spec.get("name", "channel")).lower().replace(" ", "-")
            cat_name = spec.get("category")
            ch_type  = str(spec.get("type", "text")).lower()
            if (discord.utils.get(guild.text_channels, name=name) or
                    discord.utils.get(guild.voice_channels, name=name)):
                skipped.append(name); continue
            try:
                category = None
                if cat_name:
                    category = discord.utils.get(guild.categories, name=cat_name)
                    if not category:
                        category = await guild.create_category(cat_name)
                        await asyncio.sleep(_API_DELAY)
                if ch_type == "voice":
                    ch = await guild.create_voice_channel(name=name, category=category)
                else:
                    ch = await guild.create_text_channel(name=name, category=category)
                created.append(ch.mention if hasattr(ch, "mention") else f"#{name}")
                await asyncio.sleep(_API_DELAY)
            except Exception as exc:
                failed.append(f"{name}: {exc}")
        parts = []
        if created: parts.append(f"✅ Created {len(created)}: {', '.join(created)}")
        if skipped: parts.append(f"⏭️ Existed: {', '.join(skipped)}")
        if failed:  parts.append(f"❌ Failed: {', '.join(failed)}")
        await _reply(message, "\n".join(parts) or "Nothing to do.")

    async def _delete_channel(self, message, guild: discord.Guild, params: dict) -> None:
        name = params.get("channel_name", "")
        ch   = (discord.utils.get(guild.channels, name=name) or
                next((c for c in guild.channels if c.name.lower() == name.lower()), None))
        if not ch:
            await _reply(message, f"❌ Channel `{name}` not found.")
            return
        await ch.delete(reason=f"KLAUD AI — {_author(message)}")
        await _reply(message, f"🗑️ Deleted `#{name}`")

    async def _delete_all_channels(self, message, guild: discord.Guild, params: dict) -> None:
        current = getattr(message, "channel", None)
        to_del  = [c for c in guild.channels if c != current]
        status  = await _reply(message, f"⚙️ Deleting {len(to_del)} channels...")
        deleted = failed = 0
        for ch in to_del:
            try:
                await ch.delete(reason=f"KLAUD AI bulk delete — {_author(message)}")
                deleted += 1
                await asyncio.sleep(_API_DELAY)
            except discord.HTTPException:
                failed += 1
        result = f"🗑️ Deleted **{deleted}** channel(s)."
        if failed: result += f" {failed} protected/system channels skipped."
        if status and hasattr(status, "edit"):
            try: await status.edit(content=result); return
            except Exception: pass
        await _reply(message, result)

    async def _delete_category(self, message, guild: discord.Guild, params: dict) -> None:
        name = params.get("category_name", "")
        cat  = discord.utils.get(guild.categories, name=name)
        if not cat:
            await _reply(message, f"❌ Category `{name}` not found.")
            return
        if params.get("delete_channels_inside", True):
            for ch in list(cat.channels):
                try: await ch.delete(); await asyncio.sleep(_API_DELAY)
                except Exception: pass
        await cat.delete()
        await _reply(message, f"🗑️ Deleted category **{name}**")

    async def _rename_channel(self, message, guild: discord.Guild, params: dict) -> None:
        old = params.get("old_name", "")
        new = str(params.get("new_name", "")).lower().replace(" ", "-")
        ch  = (discord.utils.get(guild.channels, name=old) or
               next((c for c in guild.channels if c.name.lower() == old.lower()), None))
        if not ch:
            await _reply(message, f"❌ Channel `{old}` not found.")
            return
        await ch.edit(name=new)
        await _reply(message, f"✅ Renamed **#{old}** → **#{new}**")

    async def _lock_channel(self, message, guild: discord.Guild, params: dict) -> None:
        name = params.get("channel_name", "CURRENT")
        ch   = (getattr(message, "channel", None) if name == "CURRENT"
                else discord.utils.get(guild.text_channels, name=name))
        if not ch: await _reply(message, f"❌ Channel `{name}` not found."); return
        ow = ch.overwrites_for(guild.default_role)
        ow.send_messages = False
        await ch.set_permissions(guild.default_role, overwrite=ow)
        try: await ch.send("🔒 This channel has been locked.")
        except Exception: pass
        await _reply(message, f"🔒 Locked {ch.mention}")

    async def _unlock_channel(self, message, guild: discord.Guild, params: dict) -> None:
        name = params.get("channel_name", "CURRENT")
        ch   = (getattr(message, "channel", None) if name == "CURRENT"
                else discord.utils.get(guild.text_channels, name=name))
        if not ch: await _reply(message, f"❌ Channel `{name}` not found."); return
        ow = ch.overwrites_for(guild.default_role)
        ow.send_messages = None
        await ch.set_permissions(guild.default_role, overwrite=ow)
        try: await ch.send("🔓 This channel has been unlocked.")
        except Exception: pass
        await _reply(message, f"🔓 Unlocked {ch.mention}")

    async def _set_channel_permissions(self, message, guild: discord.Guild, params: dict) -> None:
        ch_name   = params.get("channel_name", "")
        role_name = params.get("role_name", "")
        ch   = discord.utils.get(guild.text_channels, name=ch_name)
        role = discord.utils.get(guild.roles, name=role_name)
        if not ch:   await _reply(message, f"❌ Channel `{ch_name}` not found."); return
        if not role: await _reply(message, f"❌ Role `{role_name}` not found."); return
        ow = discord.PermissionOverwrite()
        for p in params.get("allow", []):
            try: setattr(ow, p, True)
            except AttributeError: pass
        for p in params.get("deny", []):
            try: setattr(ow, p, False)
            except AttributeError: pass
        await ch.set_permissions(role, overwrite=ow)
        await _reply(message, f"✅ Updated channel permissions for **{role.name}** in {ch.mention}")

    # ─── Role handlers ─────────────────────────────────────────────────────────

    async def _create_role(self, message, guild: discord.Guild, params: dict) -> None:
        name = params.get("name", "New Role")
        if discord.utils.get(guild.roles, name=name):
            await _reply(message, f"⏭️ Role **{name}** already exists.")
            return
        color = discord.Color.default()
        if params.get("color"):
            try: color = discord.Color(int(str(params["color"]).lstrip("#"), 16))
            except (ValueError, AttributeError): pass
        role = await guild.create_role(
            name=name, color=color,
            hoist=bool(params.get("hoist", False)),
            mentionable=bool(params.get("mentionable", False)),
        )
        await _reply(message, f"✅ Created role **{role.name}**")

    async def _bulk_create_roles(self, message, guild: discord.Guild, params: dict) -> None:
        roles = params.get("roles", [])[:20]
        if not roles: await _reply(message, "❌ No roles specified."); return
        created, skipped = [], []
        for spec in roles:
            name = spec.get("name", "Role")
            if discord.utils.get(guild.roles, name=name):
                skipped.append(name); continue
            try:
                color = discord.Color.default()
                if spec.get("color"):
                    try: color = discord.Color(int(str(spec["color"]).lstrip("#"), 16))
                    except (ValueError, AttributeError): pass
                r = await guild.create_role(
                    name=name, color=color,
                    hoist=bool(spec.get("hoist", False)),
                    mentionable=bool(spec.get("mentionable", False)),
                )
                created.append(r.name)
                await asyncio.sleep(_API_DELAY)
            except Exception as exc:
                logger.warning(f"create_role {name}: {exc}")
        parts = []
        if created: parts.append(f"✅ Created: **{', '.join(created)}**")
        if skipped: parts.append(f"⏭️ Already existed: {', '.join(skipped)}")
        await _reply(message, "\n".join(parts) or "Nothing to do.")

    async def _delete_role(self, message, guild: discord.Guild, params: dict) -> None:
        name = params.get("role_name", "")
        role = discord.utils.get(guild.roles, name=name)
        if not role: await _reply(message, f"❌ Role `{name}` not found."); return
        await role.delete()
        await _reply(message, f"🗑️ Deleted role **{name}**")

    async def _edit_role_permissions(self, message, guild: discord.Guild, params: dict) -> None:
        """Grant or revoke actual Discord permissions on a role."""
        role_name = params.get("role_name", "")
        grant     = params.get("grant", [])
        revoke    = params.get("revoke", [])

        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            await _reply(message, f"❌ Role `{role_name}` not found.")
            return

        # Build updated permissions
        perms = role.permissions
        granted_ok, revoked_ok, failed = [], [], []

        for perm in grant:
            try:
                setattr(perms, perm, True)
                granted_ok.append(perm)
            except AttributeError:
                failed.append(f"unknown permission: {perm}")

        for perm in revoke:
            try:
                setattr(perms, perm, False)
                revoked_ok.append(perm)
            except AttributeError:
                failed.append(f"unknown permission: {perm}")

        await role.edit(permissions=perms, reason=f"KLAUD AI — {_author(message)}")

        parts = [f"✅ Updated permissions for **{role.name}**:"]
        if granted_ok: parts.append(f"  ➕ Granted: {', '.join(granted_ok)}")
        if revoked_ok: parts.append(f"  ➖ Revoked: {', '.join(revoked_ok)}")
        if failed:     parts.append(f"  ⚠️ Unknown perms skipped: {', '.join(failed)}")
        await _reply(message, "\n".join(parts))

    async def _move_role_to_top(self, message, guild: discord.Guild, params: dict) -> None:
        """Move a role to just below the bot's own highest role."""
        role_name = params.get("role_name", "")
        role      = discord.utils.get(guild.roles, name=role_name)
        if not role:
            await _reply(message, f"❌ Role `{role_name}` not found.")
            return

        bot_top = guild.me.top_role
        # Place it one below the bot's top role
        target_position = max(bot_top.position - 1, 1)

        try:
            await role.edit(position=target_position, reason=f"KLAUD AI — {_author(message)}")
            await _reply(message, f"✅ Moved **{role.name}** to position {target_position} (just below my role)")
        except discord.Forbidden:
            await _reply(message, f"❌ Can't move **{role.name}** — it may already be above my role.")

    async def _assign_role(self, message, guild: discord.Guild, params: dict) -> None:
        role = discord.utils.get(guild.roles, name=params.get("role_name", ""))
        if not role:
            await _reply(message, f"❌ Role `{params.get('role_name')}` not found.")
            return
        member = _resolve_member(message, guild, params.get("user_mention", ""))
        if not member:
            await _reply(message, "❌ User not found. Mention them in the message.")
            return
        await member.add_roles(role, reason="KLAUD AI")
        await _reply(message, f"✅ Gave **{role.name}** to {member.mention}")

    async def _remove_role(self, message, guild: discord.Guild, params: dict) -> None:
        role = discord.utils.get(guild.roles, name=params.get("role_name", ""))
        if not role:
            await _reply(message, f"❌ Role `{params.get('role_name')}` not found.")
            return
        member = _resolve_member(message, guild, params.get("user_mention", ""))
        if not member:
            await _reply(message, "❌ User not found. Mention them in the message.")
            return
        await member.remove_roles(role, reason="KLAUD AI")
        await _reply(message, f"✅ Removed **{role.name}** from {member.mention}")

    # ─── Moderation handlers ───────────────────────────────────────────────────

    async def _purge_messages(self, message, guild: discord.Guild, params: dict) -> None:
        amount  = min(int(params.get("amount", 10)), 100)
        ch_name = params.get("channel_name", "CURRENT")
        ch      = (getattr(message, "channel", None) if ch_name == "CURRENT"
                   else discord.utils.get(guild.text_channels, name=ch_name))
        if not ch: await _reply(message, f"❌ Channel `{ch_name}` not found."); return
        deleted = await ch.purge(limit=amount)
        await _reply(message, f"🗑️ Purged **{len(deleted)}** messages from {ch.mention}")

    async def _kick_user(self, message, guild: discord.Guild, params: dict) -> None:
        member = _resolve_member(message, guild, params.get("user_mention", ""))
        if not member:
            await _reply(message, "❌ No user found. Mention them in the message.")
            return
        # Hierarchy check BEFORE trying
        if member.top_role >= guild.me.top_role:
            await _reply(message,
                f"❌ Can't kick **{member}** — their role is equal to or above mine.\n"
                "Drag the **KLAUD-NINJA** role above theirs in Server Settings → Roles.")
            return
        reason = params.get("reason", "Requested by admin")
        try: await member.send(f"You were kicked from **{guild.name}**. Reason: {reason}")
        except Exception: pass
        await guild.kick(member, reason=f"KLAUD AI: {reason}")
        await _reply(message, f"👢 Kicked **{member.name}**. Reason: {reason}")

    async def _ban_user(self, message, guild: discord.Guild, params: dict) -> None:
        member = _resolve_member(message, guild, params.get("user_mention", ""))
        if not member:
            await _reply(message, "❌ No user found. Mention them in the message.")
            return
        # Hierarchy check BEFORE trying
        if member.top_role >= guild.me.top_role:
            await _reply(message,
                f"❌ Can't ban **{member}** — their role is equal to or above mine.\n"
                "Drag the **KLAUD-NINJA** role above theirs in Server Settings → Roles.")
            return
        reason = params.get("reason", "Requested by admin")
        try: await member.send(f"You were banned from **{guild.name}**. Reason: {reason}")
        except Exception: pass
        await guild.ban(member, reason=f"KLAUD AI: {reason}",
                         delete_message_days=int(params.get("delete_days", 0)))
        await _reply(message, f"🔨 Banned **{member.name}**. Reason: {reason}")

    async def _unban_user(self, message, guild: discord.Guild, params: dict) -> None:
        user_id = int(params.get("user_id", 0))
        if not user_id:
            await _reply(message, "❌ Provide the user ID to unban.")
            return
        try:
            user = await self.bot.fetch_user(user_id)
            await guild.unban(user, reason=f"KLAUD AI — {_author(message)}")
            await _reply(message, f"✅ Unbanned **{user}**")
        except discord.NotFound:
            await _reply(message, "❌ User not found or not banned.")

    async def _timeout_user(self, message, guild: discord.Guild, params: dict) -> None:
        member = _resolve_member(message, guild, params.get("user_mention", ""))
        if not member:
            await _reply(message, "❌ No user found. Mention them in the message.")
            return
        if member.top_role >= guild.me.top_role:
            await _reply(message,
                f"❌ Can't timeout **{member}** — their role is above mine.")
            return
        mins   = int(params.get("duration_minutes", 10))
        reason = params.get("reason", "Requested by admin")
        await member.timeout(timedelta(minutes=mins), reason=f"KLAUD AI: {reason}")
        await _reply(message, f"🔇 Timed out **{member.name}** for {mins} minute(s). Reason: {reason}")

    async def _untimeout_user(self, message, guild: discord.Guild, params: dict) -> None:
        member = _resolve_member(message, guild, params.get("user_mention", ""))
        if not member:
            await _reply(message, "❌ No user found.")
            return
        await member.timeout(None, reason=f"KLAUD AI — {_author(message)}")
        await _reply(message, f"✅ Removed timeout from **{member.name}**")

    # ─── Setup handlers ────────────────────────────────────────────────────────

    async def _setup_verification(self, message, guild: discord.Guild, params: dict) -> None:
        ch_name   = params.get("channel_name", "verify")
        role_name = params.get("role_name", "Verified")
        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            role = await guild.create_role(name=role_name, reason="KLAUD verification")
        ch = discord.utils.get(guild.text_channels, name=ch_name)
        if not ch:
            ch = await guild.create_text_channel(ch_name)
        embed = discord.Embed(
            title="✅ Server Verification",
            description="Click below to verify and gain access to the server.",
            color=discord.Color.green(),
        )
        try:
            vc = self.bot.cogs.get("SetupVerify")
            if vc and hasattr(vc, "VerificationView"):
                await ch.send(embed=embed, view=vc.VerificationView(role_id=role.id))
            else:
                await ch.send(embed=embed)
        except Exception:
            await ch.send(embed=embed)
        await _reply(message, f"✅ Verification set up in {ch.mention} with role **{role.name}**")

    async def _setup_basic_server(self, message, guild: discord.Guild, params: dict) -> None:
        status  = await _reply(message, "⚙️ Building basic server structure...")
        created = []
        for rname, hex_col in [("Member","#808080"),("Moderator","#FF8C00"),
                                 ("VIP","#FFD700"),("Staff","#FF4500")]:
            if not discord.utils.get(guild.roles, name=rname):
                try:
                    await guild.create_role(name=rname, color=discord.Color(int(hex_col.lstrip("#"), 16)))
                    created.append(f"Role: {rname}")
                    await asyncio.sleep(_API_DELAY)
                except Exception: pass
        for cat_name, channels in {
            "📋 Information": ["rules","announcements","roles"],
            "💬 General":     ["general","off-topic","bot-commands"],
            "🛡️ Staff":       ["staff-chat","mod-log"],
        }.items():
            try:
                cat = discord.utils.get(guild.categories, name=cat_name) or \
                      await guild.create_category(cat_name)
                for ch_name in channels:
                    if not discord.utils.get(guild.text_channels, name=ch_name):
                        await guild.create_text_channel(ch_name, category=cat)
                        created.append(f"#{ch_name}")
                        await asyncio.sleep(_API_DELAY)
            except Exception as exc:
                logger.warning(f"Setup error: {exc}")
        result = f"✅ Done:\n" + "\n".join(f"  ✅ {i}" for i in created) if created else "✅ Nothing new needed."
        if status and hasattr(status, "edit"):
            try: await status.edit(content=result); return
            except Exception: pass
        await _reply(message, result)

    # ─── Help ──────────────────────────────────────────────────────────────────

    async def _send_help(self, message: discord.Message) -> None:
        embed = discord.Embed(
            title="👋 Hey! I'm KLAUD.",
            description="I can manage your server AND have a conversation. Just @mention me and talk normally.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="📁 Channels", value=(
            "`@Klaud delete all channels`\n"
            "`@Klaud create a trading category with #buy-sell and #price-check`\n"
            "`@Klaud make 3 gaming voice channels`\n"
            "`@Klaud lock this channel`\n"
            "`@Klaud purge 50 messages`"
        ), inline=False)
        embed.add_field(name="🏷️ Roles", value=(
            "`@Klaud create roles for Admin, Mod, VIP`\n"
            "`@Klaud give Moderator the ability to kick and ban`\n"
            "`@Klaud move the Admin role to the top`"
        ), inline=False)
        embed.add_field(name="🔨 Moderation", value=(
            "`@Klaud ban @user for raiding`\n"
            "`@Klaud timeout @user for 30 minutes`\n"
            "`@Klaud kick @user`"
        ), inline=False)
        embed.add_field(name="💬 Chat", value=(
            "`@Klaud what can you do?`\n"
            "`@Klaud how does verification work?`\n"
            "`@Klaud what's the best channel structure for a gaming server?`"
        ), inline=False)
        embed.set_footer(text="Powered by Groq AI • llama-3.3-70b-versatile")
        await message.reply(embed=embed, mention_author=False)

    # ─── Audit log ─────────────────────────────────────────────────────────────

    async def _log_audit(self, guild, user_id, action_type, details, success=True, error=None) -> None:
        try:
            ds = json.dumps(details)[:2000] if details else "{}"
            if self.bot.db.is_postgres():
                await self.bot.db.execute(
                    "INSERT INTO audit_log (guild_id,user_id,action_type,details,success,error_msg) "
                    "VALUES ($1,$2,$3,$4::jsonb,$5,$6)",
                    guild.id, user_id, action_type, ds, success, error)
            else:
                await self.bot.db.execute(
                    None, guild.id, user_id, action_type, ds, 1 if success else 0, error,
                    sqlite_query=(
                        "INSERT INTO audit_log (guild_id,user_id,action_type,details,success,error_msg) "
                        "VALUES (?,?,?,?,?,?)"))
        except Exception as exc:
            logger.error(f"Audit log error: {exc}")


# ─── Helpers ───────────────────────────────────────────────────────────────────

class _ResultCollector:
    def __init__(self):
        self.last = ""
        self.channel = None
        self.mentions = []
    async def reply(self, content=None, **kwargs):
        if content: self.last = str(content)
        return self
    async def edit(self, **kwargs): pass


async def _reply(message, content: str = None, **kwargs):
    """Safe reply that falls back to channel.send on NotFound."""
    if isinstance(message, _ResultCollector):
        if content: message.last = content
        return message
    try:
        return await message.reply(content, mention_author=False, **kwargs)
    except discord.NotFound:
        try: return await message.channel.send(content, **kwargs)
        except Exception: return None
    except discord.HTTPException:
        return None


def _author(message) -> str:
    return str(getattr(message, "author", "admin"))


def _resolve_member(message, guild: discord.Guild, mention_str: str) -> Optional[discord.Member]:
    if hasattr(message, "mentions") and message.mentions:
        # Skip the bot itself
        for m in message.mentions:
            if not m.bot:
                return guild.get_member(m.id)
    if mention_str:
        uid = mention_str.strip("<@!> ")
        try: return guild.get_member(int(uid))
        except ValueError: pass
    return None


async def setup(bot: KlaudBot) -> None:
    await bot.add_cog(AdminAICog(bot))
