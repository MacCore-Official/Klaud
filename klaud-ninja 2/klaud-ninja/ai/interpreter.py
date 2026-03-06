"""
KLAUD-NINJA — AI Command Interpreter
Converts the structured JSON returned by the AI into actual Discord API calls.
All action handlers live here so the cog stays thin.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Optional

import discord

log = logging.getLogger("klaud.interpreter")

# Delay between Discord API calls to stay within rate limits
_API_DELAY = 0.5


# ── Router ────────────────────────────────────────────────────────────────────

async def execute_plan(
    plan:    dict | list,
    message: discord.Message,
) -> list[str]:
    """
    Execute a parsed AI action plan inside a Discord guild.

    Args:
        plan:    Single action dict or list of action dicts from Groq.
        message: The originating Discord message (provides guild/channel context).

    Returns:
        List of human-readable result strings for the reply embed.
    """
    guild   = message.guild
    channel = message.channel

    if isinstance(plan, dict):
        plan = [plan]

    results: list[str] = []
    for action_dict in plan:
        action = str(action_dict.get("action", "unknown")).lower()
        result = await _dispatch(action, action_dict, guild, channel, message)
        results.append(result)
        await asyncio.sleep(_API_DELAY)

    return results


# ── Dispatcher ────────────────────────────────────────────────────────────────

async def _dispatch(
    action:   str,
    data:     dict,
    guild:    discord.Guild,
    channel:  discord.TextChannel,
    message:  discord.Message,
) -> str:
    handlers = {
        "create_category":   _create_category,
        "create_channels":   _create_channels,
        "create_voice":      _create_voice,
        "delete_channel":    _delete_channel,
        "rename_channel":    _rename_channel,
        "lock_channel":      _lock_channel,
        "unlock_channel":    _unlock_channel,
        "create_role":       _create_role,
        "delete_role":       _delete_role,
        "assign_role":       _assign_role,
        "send_message":      _send_message,
        "kick_user":         _kick_user,
        "ban_user":          _ban_user,
        "timeout_user":      _timeout_user,
        "purge_messages":    _purge_messages,
        "create_invite":     _create_invite,
    }
    handler = handlers.get(action)
    if handler:
        try:
            return await handler(data, guild, channel, message)
        except discord.Forbidden:
            return f"❌ `{action}` — missing permissions."
        except discord.HTTPException as exc:
            return f"❌ `{action}` — Discord error: {exc}"
        except Exception as exc:
            log.error(f"execute {action}: {exc}", exc_info=True)
            return f"❌ `{action}` — unexpected error: {exc}"
    return f"⚠️ Unknown action `{action}`."


# ── Action handlers ───────────────────────────────────────────────────────────

async def _create_category(data: dict, guild: discord.Guild, *_) -> str:
    name = str(data.get("name", "New Category"))
    if discord.utils.get(guild.categories, name=name):
        return f"⏭️ Category **{name}** already exists."
    await guild.create_category(name=name, reason=data.get("reason", "AI request"))
    return f"✅ Created category **{name}**."


async def _create_channels(data: dict, guild: discord.Guild, *_) -> str:
    cat_name = data.get("category")
    category = None
    if cat_name:
        category = discord.utils.get(guild.categories, name=cat_name)
        if not category:
            category = await guild.create_category(cat_name, reason="AI request")

    channels  = data.get("channels", [])
    created   = []
    skipped   = []
    for ch_name in channels:
        ch_name = str(ch_name).lower().replace(" ", "-")
        if discord.utils.get(guild.text_channels, name=ch_name):
            skipped.append(ch_name)
            continue
        await guild.create_text_channel(
            ch_name,
            category=category,
            reason=data.get("reason", "AI request"),
        )
        created.append(ch_name)
        await asyncio.sleep(_API_DELAY)

    parts = []
    if created:
        parts.append(f"✅ Created channels: {', '.join(f'#{c}' for c in created)}")
    if skipped:
        parts.append(f"⏭️ Already exist: {', '.join(f'#{c}' for c in skipped)}")
    return "  ".join(parts) or "Nothing to do."


async def _create_voice(data: dict, guild: discord.Guild, *_) -> str:
    cat_name = data.get("category")
    category = None
    if cat_name:
        category = discord.utils.get(guild.categories, name=cat_name)
        if not category:
            category = await guild.create_category(cat_name, reason="AI request")

    channels = data.get("channels", [])
    created  = []
    for ch_name in channels:
        await guild.create_voice_channel(
            str(ch_name),
            category=category,
            reason=data.get("reason", "AI request"),
        )
        created.append(ch_name)
        await asyncio.sleep(_API_DELAY)

    return f"✅ Created voice channels: {', '.join(created)}" if created else "Nothing to do."


async def _delete_channel(data: dict, guild: discord.Guild, *_) -> str:
    name = str(data.get("name", ""))
    ch   = discord.utils.get(guild.channels, name=name)
    if not ch:
        return f"⚠️ Channel **{name}** not found."
    await ch.delete(reason=data.get("reason", "AI request"))
    return f"🗑️ Deleted channel **{name}**."


async def _rename_channel(data: dict, guild: discord.Guild, *_) -> str:
    old = str(data.get("old_name", ""))
    new = str(data.get("new_name", "")).lower().replace(" ", "-")
    ch  = discord.utils.get(guild.channels, name=old)
    if not ch:
        return f"⚠️ Channel **{old}** not found."
    await ch.edit(name=new, reason=data.get("reason", "AI request"))
    return f"✏️ Renamed **#{old}** → **#{new}**."


async def _lock_channel(data: dict, guild: discord.Guild, channel: discord.TextChannel, *_) -> str:
    name = str(data.get("name", "")) or channel.name
    ch   = discord.utils.get(guild.text_channels, name=name) or channel
    ow   = ch.overwrites_for(guild.default_role)
    ow.send_messages = False
    await ch.set_permissions(guild.default_role, overwrite=ow, reason="AI: lock")
    await ch.send("🔒 This channel has been locked.")
    return f"🔒 Locked **#{ch.name}**."


async def _unlock_channel(data: dict, guild: discord.Guild, channel: discord.TextChannel, *_) -> str:
    name = str(data.get("name", "")) or channel.name
    ch   = discord.utils.get(guild.text_channels, name=name) or channel
    ow   = ch.overwrites_for(guild.default_role)
    ow.send_messages = None
    await ch.set_permissions(guild.default_role, overwrite=ow, reason="AI: unlock")
    await ch.send("🔓 This channel has been unlocked.")
    return f"🔓 Unlocked **#{ch.name}**."


async def _create_role(data: dict, guild: discord.Guild, *_) -> str:
    name  = str(data.get("name", "New Role"))
    color_hex = str(data.get("color", "#99aab5"))
    try:
        color = discord.Color(int(color_hex.lstrip("#"), 16))
    except ValueError:
        color = discord.Color.default()
    if discord.utils.get(guild.roles, name=name):
        return f"⏭️ Role **{name}** already exists."
    await guild.create_role(name=name, color=color, reason=data.get("reason", "AI request"))
    return f"✅ Created role **{name}**."


async def _delete_role(data: dict, guild: discord.Guild, *_) -> str:
    name = str(data.get("name", ""))
    role = discord.utils.get(guild.roles, name=name)
    if not role:
        return f"⚠️ Role **{name}** not found."
    await role.delete(reason=data.get("reason", "AI request"))
    return f"🗑️ Deleted role **{name}**."


async def _assign_role(data: dict, guild: discord.Guild, *_, message: discord.Message = None) -> str:
    role_name = str(data.get("role_name", ""))
    role      = discord.utils.get(guild.roles, name=role_name)
    if not role:
        return f"⚠️ Role **{role_name}** not found."
    # Try to get user from mentions or data
    target = None
    if message and message.mentions:
        target = message.mentions[0]
    elif data.get("user_id"):
        target = guild.get_member(int(data["user_id"]))
    if not target:
        return "⚠️ No target user found for assign_role."
    await target.add_roles(role, reason="AI request")
    return f"✅ Assigned **{role_name}** to {target.mention}."

# Fix: pass message through _dispatch
async def _assign_role_wrapper(data: dict, guild: discord.Guild, channel, message: discord.Message) -> str:
    return await _assign_role(data, guild, channel, message=message)


async def _send_message(data: dict, guild: discord.Guild, channel: discord.TextChannel, *_) -> str:
    ch_name = str(data.get("channel", "")) or channel.name
    ch      = discord.utils.get(guild.text_channels, name=ch_name) or channel
    content = str(data.get("content", "")).strip()
    if not content:
        return "⚠️ send_message: no content provided."
    await ch.send(content)
    return f"✅ Message sent in **#{ch.name}**."


async def _kick_user(data: dict, guild: discord.Guild, *_, message: discord.Message = None) -> str:
    target = None
    if message and message.mentions:
        target = message.mentions[0]
    elif data.get("user_id"):
        target = guild.get_member(int(data["user_id"]))
    if not target:
        return "⚠️ kick_user: no target user found."
    reason = data.get("reason", "AI request")
    await guild.kick(target, reason=reason)
    return f"👢 Kicked **{target}** — {reason}."


async def _ban_user(data: dict, guild: discord.Guild, *_, message: discord.Message = None) -> str:
    target = None
    if message and message.mentions:
        target = message.mentions[0]
    elif data.get("user_id"):
        target = guild.get_member(int(data["user_id"]))
    if not target:
        return "⚠️ ban_user: no target user found."
    reason = data.get("reason", "AI request")
    await guild.ban(target, reason=reason, delete_message_days=0)
    return f"🔨 Banned **{target}** — {reason}."


async def _timeout_user(data: dict, guild: discord.Guild, *_, message: discord.Message = None) -> str:
    target = None
    if message and message.mentions:
        target = message.mentions[0]
    elif data.get("user_id"):
        target = guild.get_member(int(data["user_id"]))
    if not target:
        return "⚠️ timeout_user: no target user found."
    minutes = int(data.get("duration_minutes", 10))
    reason  = data.get("reason", "AI request")
    await target.timeout(timedelta(minutes=minutes), reason=reason)
    return f"🔇 Timed out **{target}** for {minutes} minutes."


async def _purge_messages(data: dict, guild: discord.Guild, channel: discord.TextChannel, *_) -> str:
    ch_name = str(data.get("channel", "")) or channel.name
    ch      = discord.utils.get(guild.text_channels, name=ch_name) or channel
    count   = int(data.get("count", 10))
    count   = min(count, 100)  # Safety cap
    deleted = await ch.purge(limit=count)
    return f"🗑️ Purged {len(deleted)} messages in **#{ch.name}**."


async def _create_invite(data: dict, guild: discord.Guild, channel: discord.TextChannel, *_) -> str:
    ch_name = str(data.get("channel", "")) or channel.name
    ch      = discord.utils.get(guild.text_channels, name=ch_name) or channel
    invite  = await ch.create_invite(
        max_age=int(data.get("max_age_hours", 24)) * 3600,
        max_uses=int(data.get("max_uses", 0)),
        reason="AI request",
    )
    return f"🔗 Invite: {invite.url}"


# ── Context builder ───────────────────────────────────────────────────────────

def build_guild_context(guild: discord.Guild) -> str:
    """Return a compact text summary of the guild for AI context."""
    categories = ", ".join(c.name for c in guild.categories[:10]) or "none"
    text_chs   = ", ".join(f"#{c.name}" for c in guild.text_channels[:15]) or "none"
    roles      = ", ".join(r.name for r in guild.roles[:15] if not r.is_default()) or "none"
    return (
        f"Guild: {guild.name} ({guild.member_count} members)\n"
        f"Categories: {categories}\n"
        f"Text channels: {text_chs}\n"
        f"Roles: {roles}"
    )
