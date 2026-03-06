"""
KLAUD-NINJA — Admin AI Cog
Natural language server management via @mentioning the bot.
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

_REQUIRED_TIERS  = frozenset({"PRO", "ENTERPRISE"})
_ENTERPRISE_ONLY = frozenset({"setup_verification"})
_RISKY_ACTIONS   = frozenset({
    "delete_channel", "delete_category", "delete_all_channels",
    "set_permissions", "kick_user", "ban_user", "setup_basic_server",
})
_API_DELAY = 0.6


class ConfirmView(discord.ui.View):
    def __init__(self, author_id: int) -> None:
        super().__init__(timeout=30.0)
        self.author_id  = author_id
        self.confirmed: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the invoking admin can confirm.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = True
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = False
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self) -> None:
        self.confirmed = False
        self.stop()


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
            await message.reply(
                "⚠️ AI admin commands require **PRO** or **ENTERPRISE** license.\n"
                "Use `/license info` to compare tiers.", mention_author=False)
            return

        content = message.content
        for token in [f"<@{self.bot.user.id}>", f"<@!{self.bot.user.id}>"]:
            content = content.replace(token, "").strip()

        if not content:
            await self._send_help(message)
            return

        async with message.channel.typing():
            await self._process(message, content, tier)

    async def _process(self, message: discord.Message, instruction: str, tier: str) -> None:
        guild = message.guild
        guild_context = (
            f"Guild: {guild.name} ({guild.member_count} members)\n"
            f"Categories: {[c.name for c in guild.categories[:15]]}\n"
            f"Text channels: {[c.name for c in guild.text_channels[:20]]}\n"
            f"Voice channels: {[c.name for c in guild.voice_channels[:10]]}\n"
            f"Roles: {[r.name for r in guild.roles[:20] if not r.is_default()]}\n"
            f"Current channel: {message.channel.name}"
        )

        try:
            decision = await asyncio.wait_for(
                self.bot.groq.parse_admin_command(instruction, guild_context),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            await message.reply("⏱️ AI took too long. Please try again.", mention_author=False)
            return
        except Exception as exc:
            logger.error(f"Admin AI error: {exc}", exc_info=True)
            await message.reply("❌ Error processing your request.", mention_author=False)
            return

        if not decision.valid or decision.action_type == "unknown":
            reason = decision.explanation or "I couldn't understand that. Please be more specific."
            await message.reply(f"🤔 {reason}", mention_author=False)
            return

        if decision.action_type in _ENTERPRISE_ONLY and tier != "ENTERPRISE":
            await message.reply(
                f"⚠️ **{decision.action_type}** requires **ENTERPRISE** license.",
                mention_author=False)
            return

        # Check if confirmation needed
        needs_confirm = decision.confirmation_required or decision.action_type in _RISKY_ACTIONS
        if decision.action_type == "multi_action":
            sub = [a.get("action_type","") for a in decision.parameters.get("actions",[])]
            if any(a in _RISKY_ACTIONS for a in sub):
                needs_confirm = True

        if needs_confirm:
            confirmed = await self._confirm(message, decision)
            if not confirmed:
                await message.reply("❌ Cancelled.", mention_author=False)
                return

        await self._execute(message, decision)

    async def _confirm(self, message: discord.Message, decision: AdminCommandDecision) -> bool:
        embed = discord.Embed(
            title="⚠️ Confirm Action",
            description=f"**{decision.explanation}**\n\nThis may be hard to undo. Continue?",
            color=discord.Color.orange(),
        )
        view  = ConfirmView(author_id=message.author.id)
        reply = await message.reply(embed=embed, view=view, mention_author=False)
        await view.wait()
        try:
            await reply.edit(view=view)
        except discord.HTTPException:
            pass
        return view.confirmed is True

    async def _execute(self, message: discord.Message, decision: AdminCommandDecision) -> None:
        guild  = message.guild
        action = decision.action_type
        params = decision.parameters
        router = {
            "create_category":      self._create_category,
            "create_channel":       self._create_channel,
            "bulk_create_channels": self._bulk_create_channels,
            "delete_channel":       self._delete_channel,
            "delete_all_channels":  self._delete_all_channels,
            "delete_category":      self._delete_category,
            "rename_channel":       self._rename_channel,
            "set_permissions":      self._set_permissions,
            "create_role":          self._create_role,
            "bulk_create_roles":    self._bulk_create_roles,
            "delete_role":          self._delete_role,
            "assign_role":          self._assign_role,
            "lock_channel":         self._lock_channel,
            "unlock_channel":       self._unlock_channel,
            "purge_messages":       self._purge_messages,
            "kick_user":            self._kick_user,
            "ban_user":             self._ban_user,
            "timeout_user":         self._timeout_user,
            "setup_verification":   self._setup_verification,
            "setup_basic_server":   self._setup_basic_server,
            "multi_action":         self._multi_action,
        }
        handler = router.get(action)
        if not handler:
            await message.reply(f"🤔 I don't know how to do `{action}` yet.", mention_author=False)
            return
        try:
            await handler(message, guild, params)
            await self._log_audit(guild, message.author.id, action, params, success=True)
        except discord.Forbidden:
            await message.reply(
                "❌ I'm missing permissions. Make sure my role is above what I'm managing.",
                mention_author=False)
        except Exception as exc:
            logger.error(f"Action error ({action}): {exc}", exc_info=True)
            await message.reply(f"❌ Failed: {exc}", mention_author=False)

    # ── Multi-action ──────────────────────────────────────────────────────────

    async def _multi_action(self, message, guild: discord.Guild, params: dict) -> None:
        actions = params.get("actions", [])
        if not actions:
            await _reply(message, "❌ No actions in plan.")
            return
        status = await _reply(message, f"⚙️ Executing {len(actions)} action(s)...")
        results = []
        router = {
            "create_category":      self._create_category,
            "create_channel":       self._create_channel,
            "bulk_create_channels": self._bulk_create_channels,
            "delete_channel":       self._delete_channel,
            "delete_all_channels":  self._delete_all_channels,
            "delete_category":      self._delete_category,
            "rename_channel":       self._rename_channel,
            "create_role":          self._create_role,
            "bulk_create_roles":    self._bulk_create_roles,
            "lock_channel":         self._lock_channel,
            "unlock_channel":       self._unlock_channel,
            "set_permissions":      self._set_permissions,
        }
        for step in actions:
            at = step.get("action_type", "")
            sp = step.get("parameters", {})
            handler = router.get(at)
            if handler:
                try:
                    col = _ResultCollector()
                    await handler(col, guild, sp)
                    results.append(f"✅ {at}: {col.last or 'done'}")
                except Exception as exc:
                    results.append(f"❌ {at}: {exc}")
            else:
                results.append(f"⚠️ Unknown: {at}")
            await asyncio.sleep(_API_DELAY)

        summary = "\n".join(results)
        result  = f"✅ **Done!**\n{summary}"
        if status and hasattr(status, "edit"):
            try:
                await status.edit(content=result)
                return
            except Exception:
                pass
        await _reply(message, result)

    # ── Action handlers ───────────────────────────────────────────────────────

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
            ch = await guild.create_text_channel(name=name, category=category,
                                                  topic=topic[:1024] or None)
            await _reply(message, f"✅ Created channel {ch.mention}")

    async def _bulk_create_channels(self, message, guild: discord.Guild, params: dict) -> None:
        channels = params.get("channels", [])[:15]
        if not channels:
            await _reply(message, "❌ No channels specified.")
            return
        created, skipped, failed = [], [], []
        for spec in channels:
            name     = str(spec.get("name","channel")).lower().replace(" ","-")
            cat_name = spec.get("category")
            ch_type  = str(spec.get("type","text")).lower()
            if (discord.utils.get(guild.text_channels, name=name) or
                discord.utils.get(guild.voice_channels, name=name)):
                skipped.append(name)
                continue
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
                created.append(ch.mention if hasattr(ch,"mention") else f"#{name}")
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
                next((c for c in guild.channels if c.name.lower()==name.lower()), None))
        if not ch:
            await _reply(message, f"❌ Channel `{name}` not found.")
            return
        await ch.delete(reason=f"KLAUD AI — {_author(message)}")
        await _reply(message, f"🗑️ Deleted `#{name}`")

    async def _delete_all_channels(self, message, guild: discord.Guild, params: dict) -> None:
        current = getattr(message, "channel", None)
        to_del  = [c for c in guild.channels if c != current]
        status  = await _reply(message, f"⚙️ Deleting {len(to_del)} channels...")
        deleted = 0
        failed  = 0
        for ch in to_del:
            try:
                await ch.delete(reason=f"KLAUD AI bulk delete — {_author(message)}")
                deleted += 1
                await asyncio.sleep(_API_DELAY)
            except discord.HTTPException:
                failed += 1
        result = f"🗑️ Deleted **{deleted}** channel(s)."
        if failed: result += f" {failed} could not be deleted (system/protected)."
        if status and hasattr(status, "edit"):
            try:
                await status.edit(content=result)
                return
            except Exception:
                pass
        await _reply(message, result)

    async def _delete_category(self, message, guild: discord.Guild, params: dict) -> None:
        name = params.get("category_name", "")
        cat  = discord.utils.get(guild.categories, name=name)
        if not cat:
            await _reply(message, f"❌ Category `{name}` not found.")
            return
        if params.get("delete_channels_inside", True):
            for ch in list(cat.channels):
                try:
                    await ch.delete(reason="KLAUD AI category delete")
                    await asyncio.sleep(_API_DELAY)
                except Exception:
                    pass
        await cat.delete(reason=f"KLAUD AI — {_author(message)}")
        await _reply(message, f"🗑️ Deleted category **{name}**")

    async def _rename_channel(self, message, guild: discord.Guild, params: dict) -> None:
        old = params.get("old_name","")
        new = str(params.get("new_name","")).lower().replace(" ","-")
        ch  = (discord.utils.get(guild.channels, name=old) or
               next((c for c in guild.channels if c.name.lower()==old.lower()), None))
        if not ch:
            await _reply(message, f"❌ Channel `{old}` not found.")
            return
        await ch.edit(name=new)
        await _reply(message, f"✅ Renamed **#{old}** → **#{new}**")

    async def _set_permissions(self, message, guild: discord.Guild, params: dict) -> None:
        ch_name   = params.get("channel_name","")
        role_name = params.get("role_name","")
        ch   = discord.utils.get(guild.text_channels, name=ch_name)
        role = discord.utils.get(guild.roles, name=role_name)
        if not ch:   await _reply(message, f"❌ Channel `{ch_name}` not found."); return
        if not role: await _reply(message, f"❌ Role `{role_name}` not found."); return
        ow = discord.PermissionOverwrite()
        for p in params.get("allow",[]):
            try: setattr(ow, p, True)
            except AttributeError: pass
        for p in params.get("deny",[]):
            try: setattr(ow, p, False)
            except AttributeError: pass
        await ch.set_permissions(role, overwrite=ow)
        await _reply(message, f"✅ Updated permissions for **{role.name}** in {ch.mention}")

    async def _create_role(self, message, guild: discord.Guild, params: dict) -> None:
        name = params.get("name","New Role")
        if discord.utils.get(guild.roles, name=name):
            await _reply(message, f"⏭️ Role **{name}** already exists.")
            return
        color = discord.Color.default()
        if params.get("color"):
            try: color = discord.Color(int(str(params["color"]).lstrip("#"), 16))
            except (ValueError, AttributeError): pass
        role = await guild.create_role(name=name, color=color,
                                        hoist=bool(params.get("hoist", False)))
        await _reply(message, f"✅ Created role **{role.name}**")

    async def _bulk_create_roles(self, message, guild: discord.Guild, params: dict) -> None:
        roles = params.get("roles", [])[:20]
        if not roles:
            await _reply(message, "❌ No roles specified.")
            return
        created, skipped = [], []
        for spec in roles:
            name = spec.get("name","Role")
            if discord.utils.get(guild.roles, name=name):
                skipped.append(name)
                continue
            try:
                color = discord.Color.default()
                if spec.get("color"):
                    try: color = discord.Color(int(str(spec["color"]).lstrip("#"), 16))
                    except (ValueError, AttributeError): pass
                r = await guild.create_role(name=name, color=color,
                                             hoist=bool(spec.get("hoist",False)))
                created.append(r.name)
                await asyncio.sleep(_API_DELAY)
            except Exception as exc:
                logger.warning(f"create_role {name}: {exc}")
        parts = []
        if created: parts.append(f"✅ Created {len(created)} role(s): **{', '.join(created)}**")
        if skipped: parts.append(f"⏭️ Already existed: {', '.join(skipped)}")
        await _reply(message, "\n".join(parts) or "Nothing to do.")

    async def _delete_role(self, message, guild: discord.Guild, params: dict) -> None:
        name = params.get("role_name","")
        role = discord.utils.get(guild.roles, name=name)
        if not role:
            await _reply(message, f"❌ Role `{name}` not found.")
            return
        await role.delete(reason=f"KLAUD AI — {_author(message)}")
        await _reply(message, f"🗑️ Deleted role **{name}**")

    async def _assign_role(self, message, guild: discord.Guild, params: dict) -> None:
        role = discord.utils.get(guild.roles, name=params.get("role_name",""))
        if not role:
            await _reply(message, f"❌ Role `{params.get('role_name')}` not found.")
            return
        member = _resolve_member(message, guild, params.get("user_mention",""))
        if not member:
            await _reply(message, "❌ User not found. Mention them in the message.")
            return
        await member.add_roles(role, reason="KLAUD AI")
        await _reply(message, f"✅ Assigned **{role.name}** to {member.mention}")

    async def _lock_channel(self, message, guild: discord.Guild, params: dict) -> None:
        name = params.get("channel_name","CURRENT")
        ch   = (getattr(message,"channel",None) if name == "CURRENT"
                else discord.utils.get(guild.text_channels, name=name))
        if not ch:
            await _reply(message, f"❌ Channel `{name}` not found.")
            return
        ow = ch.overwrites_for(guild.default_role)
        ow.send_messages = False
        await ch.set_permissions(guild.default_role, overwrite=ow)
        try: await ch.send("🔒 This channel has been locked.")
        except Exception: pass
        await _reply(message, f"🔒 Locked {ch.mention}")

    async def _unlock_channel(self, message, guild: discord.Guild, params: dict) -> None:
        name = params.get("channel_name","CURRENT")
        ch   = (getattr(message,"channel",None) if name == "CURRENT"
                else discord.utils.get(guild.text_channels, name=name))
        if not ch:
            await _reply(message, f"❌ Channel `{name}` not found.")
            return
        ow = ch.overwrites_for(guild.default_role)
        ow.send_messages = None
        await ch.set_permissions(guild.default_role, overwrite=ow)
        try: await ch.send("🔓 This channel has been unlocked.")
        except Exception: pass
        await _reply(message, f"🔓 Unlocked {ch.mention}")

    async def _purge_messages(self, message, guild: discord.Guild, params: dict) -> None:
        amount  = min(int(params.get("amount", 10)), 100)
        ch_name = params.get("channel_name","CURRENT")
        ch      = (getattr(message,"channel",None) if ch_name == "CURRENT"
                   else discord.utils.get(guild.text_channels, name=ch_name))
        if not ch:
            await _reply(message, f"❌ Channel `{ch_name}` not found.")
            return
        deleted = await ch.purge(limit=amount)
        await _reply(message, f"🗑️ Purged **{len(deleted)}** messages from {ch.mention}")

    async def _kick_user(self, message, guild: discord.Guild, params: dict) -> None:
        member = _resolve_member(message, guild, params.get("user_mention",""))
        if not member:
            await _reply(message, "❌ No user found. Mention them in the message.")
            return
        reason = params.get("reason","Requested by admin")
        try: await member.send(f"You were kicked from **{guild.name}**. Reason: {reason}")
        except Exception: pass
        await guild.kick(member, reason=f"KLAUD AI: {reason}")
        await _reply(message, f"👢 Kicked **{member}**")

    async def _ban_user(self, message, guild: discord.Guild, params: dict) -> None:
        member = _resolve_member(message, guild, params.get("user_mention",""))
        if not member:
            await _reply(message, "❌ No user found. Mention them in the message.")
            return
        reason = params.get("reason","Requested by admin")
        try: await member.send(f"You were banned from **{guild.name}**. Reason: {reason}")
        except Exception: pass
        await guild.ban(member, reason=f"KLAUD AI: {reason}",
                         delete_message_days=int(params.get("delete_days",0)))
        await _reply(message, f"🔨 Banned **{member}**")

    async def _timeout_user(self, message, guild: discord.Guild, params: dict) -> None:
        member = _resolve_member(message, guild, params.get("user_mention",""))
        if not member:
            await _reply(message, "❌ No user found. Mention them in the message.")
            return
        mins   = int(params.get("duration_minutes", 10))
        reason = params.get("reason","Requested by admin")
        await member.timeout(timedelta(minutes=mins), reason=f"KLAUD AI: {reason}")
        await _reply(message, f"🔇 Timed out **{member}** for {mins} minute(s)")

    async def _setup_verification(self, message, guild: discord.Guild, params: dict) -> None:
        ch_name   = params.get("channel_name","verify")
        role_name = params.get("role_name","Verified")
        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            role = await guild.create_role(name=role_name, reason="KLAUD verification")
        ch = discord.utils.get(guild.text_channels, name=ch_name)
        if not ch:
            ch = await guild.create_text_channel(ch_name, reason="KLAUD verification")
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
                    await guild.create_role(
                        name=rname,
                        color=discord.Color(int(hex_col.lstrip("#"), 16)))
                    created.append(f"Role: {rname}")
                    await asyncio.sleep(_API_DELAY)
                except Exception:
                    pass
        structure = {
            "📋 Information": ["rules","announcements","roles"],
            "💬 General":     ["general","off-topic","bot-commands"],
            "🛡️ Staff":       ["staff-chat","mod-log"],
        }
        for cat_name, channels in structure.items():
            try:
                cat = (discord.utils.get(guild.categories, name=cat_name) or
                       await guild.create_category(cat_name))
                for ch_name in channels:
                    if not discord.utils.get(guild.text_channels, name=ch_name):
                        await guild.create_text_channel(ch_name, category=cat)
                        created.append(f"#{ch_name}")
                        await asyncio.sleep(_API_DELAY)
            except Exception as exc:
                logger.warning(f"Setup error: {exc}")
        summary = "\n".join(f"  ✅ {i}" for i in created)
        result  = f"✅ Done:\n{summary or '(nothing new needed)'}"
        if status and hasattr(status, "edit"):
            try: await status.edit(content=result); return
            except Exception: pass
        await _reply(message, result)

    async def _send_help(self, message: discord.Message) -> None:
        embed = discord.Embed(
            title="🤖 KLAUD AI — Server Management",
            description="Mention me with any instruction in plain English.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="📁 Channels & Categories", value=(
            "`@Klaud delete all channels`\n"
            "`@Klaud create a trading category with channels buy-sell, price-check, middleman`\n"
            "`@Klaud make 3 gaming voice channels`\n"
            "`@Klaud rename #general to #chat`\n"
            "`@Klaud lock this channel`\n"
            "`@Klaud purge 50 messages`"
        ), inline=False)
        embed.add_field(name="🏷️ Roles", value=(
            "`@Klaud create roles for Admin, Moderator, VIP`\n"
            "`@Klaud create a green Verified role`"
        ), inline=False)
        embed.add_field(name="🔨 Moderation", value=(
            "`@Klaud kick @user for spamming`\n"
            "`@Klaud timeout @user for 30 minutes`\n"
            "`@Klaud ban @user for raiding`"
        ), inline=False)
        embed.add_field(name="🔧 Setup", value=(
            "`@Klaud set up verification`\n"
            "`@Klaud build a basic server structure`"
        ), inline=False)
        embed.set_footer(text="Powered by Groq AI (llama-3.3-70b-versatile)")
        await message.reply(embed=embed, mention_author=False)

    async def _log_audit(self, guild, user_id, action_type, details,
                          success=True, error=None) -> None:
        try:
            ds = json.dumps(details)[:2000] if details else "{}"
            if self.bot.db.is_postgres():
                await self.bot.db.execute(
                    "INSERT INTO audit_log (guild_id,user_id,action_type,details,success,error_msg) "
                    "VALUES ($1,$2,$3,$4::jsonb,$5,$6)",
                    guild.id, user_id, action_type, ds, success, error)
            else:
                await self.bot.db.execute(
                    None, guild.id, user_id, action_type, ds,
                    1 if success else 0, error,
                    sqlite_query=(
                        "INSERT INTO audit_log (guild_id,user_id,action_type,details,success,error_msg) "
                        "VALUES (?,?,?,?,?,?)"))
        except Exception as exc:
            logger.error(f"Audit log error: {exc}")


# ── Utilities ──────────────────────────────────────────────────────────────────

class _ResultCollector:
    def __init__(self):
        self.last    = ""
        self.channel = None
        self.mentions = []
    async def reply(self, content=None, **kwargs):
        if content: self.last = str(content)
        return self
    async def edit(self, **kwargs): pass


async def _reply(message, content: str):
    if isinstance(message, _ResultCollector):
        message.last = content
        return message
    try:
        return await message.reply(content, mention_author=False)
    except discord.NotFound:
        try:
            return await message.channel.send(content)
        except Exception:
            return None
    except discord.HTTPException:
        return None


def _author(message) -> str:
    return str(getattr(message, "author", "admin"))


def _resolve_member(message, guild: discord.Guild, mention_str: str) -> Optional[discord.Member]:
    if hasattr(message, "mentions") and message.mentions:
        return message.mentions[0]
    if mention_str:
        uid = mention_str.strip("<@!> ")
        try:
            return guild.get_member(int(uid))
        except ValueError:
            pass
    return None


async def setup(bot: KlaudBot) -> None:
    await bot.add_cog(AdminAICog(bot))
