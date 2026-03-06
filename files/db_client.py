"""
database/db_client.py — Supabase client and all DB interactions for Klaud Bot.

Tables expected in Supabase:
  licenses       (id, guild_id, license_key, owner_id, activated_at, expires_at, active)
  warnings       (id, guild_id, user_id, moderator_id, reason, created_at)
  mod_logs       (id, guild_id, action, target_id, actor_id, detail, created_at)
  custom_prompts (id, guild_id, prompt_name, prompt_text, created_by, updated_at)
  server_templates (id, guild_id, template_name, template_json, created_at)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from supabase import create_client, Client
import config

log = logging.getLogger(__name__)

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    return _client


# ── License helpers ────────────────────────────────────────────────────────────

async def get_license(guild_id: int) -> dict | None:
    """Fetch the active license row for a guild."""
    try:
        res = (
            get_client()
            .table("licenses")
            .select("*")
            .eq("guild_id", str(guild_id))
            .eq("active", True)
            .maybe_single()
            .execute()
        )
        return res.data
    except Exception as exc:
        log.error("get_license error: %s", exc)
        return None


async def activate_license(
    guild_id: int, license_key: str, owner_id: int, expires_at: datetime | None = None
) -> dict | None:
    """Insert or update a license record for a guild."""
    try:
        payload: dict[str, Any] = {
            "guild_id": str(guild_id),
            "license_key": license_key,
            "owner_id": str(owner_id),
            "activated_at": datetime.now(timezone.utc).isoformat(),
            "active": True,
        }
        if expires_at:
            payload["expires_at"] = expires_at.isoformat()

        res = (
            get_client()
            .table("licenses")
            .upsert(payload, on_conflict="guild_id")
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as exc:
        log.error("activate_license error: %s", exc)
        return None


async def deactivate_license(guild_id: int) -> bool:
    try:
        get_client().table("licenses").update({"active": False}).eq(
            "guild_id", str(guild_id)
        ).execute()
        return True
    except Exception as exc:
        log.error("deactivate_license error: %s", exc)
        return False


async def transfer_license(guild_id: int, new_owner_id: int) -> bool:
    try:
        get_client().table("licenses").update({"owner_id": str(new_owner_id)}).eq(
            "guild_id", str(guild_id)
        ).execute()
        return True
    except Exception as exc:
        log.error("transfer_license error: %s", exc)
        return False


# ── Warning helpers ────────────────────────────────────────────────────────────

async def add_warning(
    guild_id: int, user_id: int, moderator_id: int, reason: str
) -> dict | None:
    try:
        res = (
            get_client()
            .table("warnings")
            .insert(
                {
                    "guild_id": str(guild_id),
                    "user_id": str(user_id),
                    "moderator_id": str(moderator_id),
                    "reason": reason,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as exc:
        log.error("add_warning error: %s", exc)
        return None


async def get_warnings(guild_id: int, user_id: int) -> list[dict]:
    try:
        res = (
            get_client()
            .table("warnings")
            .select("*")
            .eq("guild_id", str(guild_id))
            .eq("user_id", str(user_id))
            .order("created_at")
            .execute()
        )
        return res.data or []
    except Exception as exc:
        log.error("get_warnings error: %s", exc)
        return []


async def clear_warnings(guild_id: int, user_id: int) -> int:
    """Delete all warnings for a user in a guild. Returns count deleted."""
    try:
        existing = await get_warnings(guild_id, user_id)
        get_client().table("warnings").delete().eq("guild_id", str(guild_id)).eq(
            "user_id", str(user_id)
        ).execute()
        return len(existing)
    except Exception as exc:
        log.error("clear_warnings error: %s", exc)
        return 0


# ── Mod log helpers ────────────────────────────────────────────────────────────

async def log_action(
    guild_id: int, action: str, target_id: int, actor_id: int, detail: str = ""
) -> None:
    try:
        get_client().table("mod_logs").insert(
            {
                "guild_id": str(guild_id),
                "action": action,
                "target_id": str(target_id),
                "actor_id": str(actor_id),
                "detail": detail,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()
    except Exception as exc:
        log.error("log_action error: %s", exc)


async def get_mod_logs(guild_id: int, limit: int = 50) -> list[dict]:
    try:
        res = (
            get_client()
            .table("mod_logs")
            .select("*")
            .eq("guild_id", str(guild_id))
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception as exc:
        log.error("get_mod_logs error: %s", exc)
        return []


# ── Custom prompt helpers ──────────────────────────────────────────────────────

async def get_prompts(guild_id: int) -> list[dict]:
    try:
        res = (
            get_client()
            .table("custom_prompts")
            .select("*")
            .eq("guild_id", str(guild_id))
            .execute()
        )
        return res.data or []
    except Exception as exc:
        log.error("get_prompts error: %s", exc)
        return []


async def upsert_prompt(
    guild_id: int, prompt_name: str, prompt_text: str, created_by: int
) -> dict | None:
    try:
        res = (
            get_client()
            .table("custom_prompts")
            .upsert(
                {
                    "guild_id": str(guild_id),
                    "prompt_name": prompt_name,
                    "prompt_text": prompt_text,
                    "created_by": str(created_by),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="guild_id,prompt_name",
            )
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as exc:
        log.error("upsert_prompt error: %s", exc)
        return None


async def delete_prompt(guild_id: int, prompt_name: str) -> bool:
    try:
        get_client().table("custom_prompts").delete().eq(
            "guild_id", str(guild_id)
        ).eq("prompt_name", prompt_name).execute()
        return True
    except Exception as exc:
        log.error("delete_prompt error: %s", exc)
        return False


# ── Server template helpers ────────────────────────────────────────────────────

async def save_template(
    guild_id: int, template_name: str, template_json: dict
) -> dict | None:
    import json
    try:
        res = (
            get_client()
            .table("server_templates")
            .upsert(
                {
                    "guild_id": str(guild_id),
                    "template_name": template_name,
                    "template_json": json.dumps(template_json),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="guild_id,template_name",
            )
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as exc:
        log.error("save_template error: %s", exc)
        return None


async def load_template(guild_id: int, template_name: str) -> dict | None:
    import json
    try:
        res = (
            get_client()
            .table("server_templates")
            .select("*")
            .eq("guild_id", str(guild_id))
            .eq("template_name", template_name)
            .maybe_single()
            .execute()
        )
        if res.data:
            res.data["template_json"] = json.loads(res.data["template_json"])
        return res.data
    except Exception as exc:
        log.error("load_template error: %s", exc)
        return None
