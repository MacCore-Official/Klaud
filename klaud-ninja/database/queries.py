"""
KLAUD-NINJA — Database Queries
All Supabase read/write operations for the bot.
Every function is async-safe (Supabase SDK calls are synchronous but fast,
so they run directly; wrap in asyncio.to_thread for heavy workloads).

Tables
──────
guild_settings   — per-server config
infractions      — moderation history
ai_logs          — full AI action audit trail
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from database.supabase_client import get_client

log = logging.getLogger("klaud.database")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _run(fn, *args, **kwargs):
    """Run a synchronous Supabase call in a thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


# ── guild_settings ────────────────────────────────────────────────────────────

async def get_guild_settings(guild_id: int) -> Optional[dict]:
    """Fetch settings for a guild. Returns None if not found."""
    try:
        db = get_client()
        res = await _run(
            lambda: db.table("guild_settings")
                      .select("*")
                      .eq("guild_id", str(guild_id))
                      .maybe_single()
                      .execute()
        )
        return res.data
    except Exception as exc:
        log.error(f"get_guild_settings({guild_id}): {exc}")
        return None


async def upsert_guild_settings(guild_id: int, **fields: Any) -> bool:
    """
    Insert or update settings for a guild.
    Pass keyword arguments matching the column names:
        upsert_guild_settings(123, moderation_level="HIGH", ai_enabled=True)
    """
    try:
        db     = get_client()
        record = {"guild_id": str(guild_id), "updated_at": _now(), **{k: v for k, v in fields.items()}}
        await _run(
            lambda: db.table("guild_settings")
                      .upsert(record, on_conflict="guild_id")
                      .execute()
        )
        return True
    except Exception as exc:
        log.error(f"upsert_guild_settings({guild_id}): {exc}")
        return False


async def get_or_create_guild_settings(guild_id: int) -> dict:
    """Return settings row, creating defaults if absent."""
    row = await get_guild_settings(guild_id)
    if row:
        return row
    defaults = {
        "guild_id":         str(guild_id),
        "moderation_level": "MEDIUM",
        "log_channel":      None,
        "ai_enabled":       True,
        "created_at":       _now(),
        "updated_at":       _now(),
    }
    try:
        db = get_client()
        await _run(lambda: db.table("guild_settings").insert(defaults).execute())
    except Exception as exc:
        log.error(f"get_or_create_guild_settings insert({guild_id}): {exc}")
    return defaults


# ── infractions ───────────────────────────────────────────────────────────────

async def log_infraction(
    guild_id: int,
    user_id:  int,
    reason:   str,
    action:   str,
) -> bool:
    """Insert a new moderation infraction record."""
    try:
        db = get_client()
        await _run(
            lambda: db.table("infractions")
                      .insert({
                          "guild_id":  str(guild_id),
                          "user_id":   str(user_id),
                          "reason":    reason,
                          "action":    action,
                          "timestamp": _now(),
                      })
                      .execute()
        )
        return True
    except Exception as exc:
        log.error(f"log_infraction: {exc}")
        return False


async def get_user_infractions(guild_id: int, user_id: int, limit: int = 10) -> list[dict]:
    """Return the most recent infractions for a user in a guild."""
    try:
        db  = get_client()
        res = await _run(
            lambda: db.table("infractions")
                      .select("*")
                      .eq("guild_id", str(guild_id))
                      .eq("user_id",  str(user_id))
                      .order("timestamp", desc=True)
                      .limit(limit)
                      .execute()
        )
        return res.data or []
    except Exception as exc:
        log.error(f"get_user_infractions: {exc}")
        return []


# ── ai_logs ───────────────────────────────────────────────────────────────────

async def log_ai_action(
    guild_id:         int,
    input_text:       str,
    ai_response:      str,
    executed_action:  str,
) -> bool:
    """Insert a full AI action audit record."""
    try:
        db = get_client()
        await _run(
            lambda: db.table("ai_logs")
                      .insert({
                          "guild_id":        str(guild_id),
                          "input":           input_text[:2000],
                          "ai_response":     ai_response[:4000],
                          "executed_action": executed_action[:500],
                          "timestamp":       _now(),
                      })
                      .execute()
        )
        return True
    except Exception as exc:
        log.error(f"log_ai_action: {exc}")
        return False


async def get_ai_logs(guild_id: int, limit: int = 20) -> list[dict]:
    """Return the most recent AI action logs for a guild."""
    try:
        db  = get_client()
        res = await _run(
            lambda: db.table("ai_logs")
                      .select("*")
                      .eq("guild_id", str(guild_id))
                      .order("timestamp", desc=True)
                      .limit(limit)
                      .execute()
        )
        return res.data or []
    except Exception as exc:
        log.error(f"get_ai_logs: {exc}")
        return []
