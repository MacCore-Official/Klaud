"""
ai/prompt_manager.py — Manages per-guild custom AI prompts stored in Supabase.

Custom prompts let admins define behaviour like:
  "If someone swears, warn them. If they repeat it, timeout for 10 minutes."
  "Automatically greet new members in #welcome."

The prompt manager caches prompts in memory (per guild) and refreshes them
from Supabase every CACHE_TTL seconds to avoid hammering the DB on every message.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from database import db_client

log = logging.getLogger(__name__)

CACHE_TTL = 300  # seconds


class PromptManager:
    def __init__(self) -> None:
        # {guild_id: {"prompts": [...], "fetched_at": float}}
        self._cache: dict[int, dict] = {}

    # ── Internal cache helpers ─────────────────────────────────────────────────

    def _is_stale(self, guild_id: int) -> bool:
        entry = self._cache.get(guild_id)
        if not entry:
            return True
        return (time.monotonic() - entry["fetched_at"]) > CACHE_TTL

    def _store(self, guild_id: int, prompts: list[dict]) -> None:
        self._cache[guild_id] = {"prompts": prompts, "fetched_at": time.monotonic()}

    def invalidate(self, guild_id: int) -> None:
        """Force next access to re-fetch from Supabase."""
        self._cache.pop(guild_id, None)

    # ── Public API ─────────────────────────────────────────────────────────────

    async def get_all(self, guild_id: int) -> list[dict]:
        """Return all custom prompts for the guild, refreshing cache if needed."""
        if self._is_stale(guild_id):
            prompts = await db_client.get_prompts(guild_id)
            self._store(guild_id, prompts)
        return self._cache[guild_id]["prompts"]

    async def get_combined_rules(self, guild_id: int) -> str:
        """
        Return all prompts combined into a single rule string suitable for
        injecting into the moderation system prompt.
        """
        prompts = await self.get_all(guild_id)
        if not prompts:
            return "No custom rules set."
        lines = [f"- [{p['prompt_name']}] {p['prompt_text']}" for p in prompts]
        return "\n".join(lines)

    async def add_prompt(
        self, guild_id: int, name: str, text: str, created_by: int
    ) -> Optional[dict]:
        result = await db_client.upsert_prompt(guild_id, name, text, created_by)
        self.invalidate(guild_id)
        return result

    async def remove_prompt(self, guild_id: int, name: str) -> bool:
        ok = await db_client.delete_prompt(guild_id, name)
        self.invalidate(guild_id)
        return ok


# Singleton — import this instance everywhere
prompt_manager = PromptManager()
