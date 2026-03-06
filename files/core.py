"""
ai/core.py — Groq AI integration for Klaud Bot.

Provides:
  - ask()           : single-turn chat completion
  - moderation_check(): classify a message for policy violations
  - build_server_plan(): convert a free-text instruction into a structured plan
"""

from __future__ import annotations

import json
import logging
from typing import Any

from groq import AsyncGroq
import config

log = logging.getLogger(__name__)

_groq: AsyncGroq | None = None


def get_groq() -> AsyncGroq:
    global _groq
    if _groq is None:
        _groq = AsyncGroq(api_key=config.GROQ_API_KEY)
    return _groq


# ── Base chat helper ───────────────────────────────────────────────────────────

async def ask(
    system: str,
    user: str,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> str:
    """Send a chat message to Groq and return the assistant reply as a string."""
    try:
        resp = await get_groq().chat.completions.create(
            model=model or config.GROQ_MODEL,
            max_tokens=max_tokens or config.GROQ_MAX_TOKENS,
            temperature=temperature if temperature is not None else config.GROQ_TEMPERATURE,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:
        log.error("Groq ask() error: %s", exc)
        return ""


# ── Moderation classifier ──────────────────────────────────────────────────────

MODERATION_SYSTEM = """
You are a strict Discord moderation AI. Analyse the provided message and respond ONLY with a
valid JSON object — no markdown, no extra text.

Schema:
{
  "violation": true | false,
  "categories": ["swearing"|"harassment"|"spam"|"toxicity"|"scam"|"nsfw"|"hate"],
  "severity": "low" | "medium" | "high",
  "action": "none" | "warn" | "delete" | "timeout" | "ban",
  "reason": "<one sentence explanation>"
}

Custom rules provided by the server admin (may override defaults):
{custom_rules}
"""


async def moderation_check(
    message_content: str,
    custom_rules: str = "No custom rules set.",
) -> dict[str, Any]:
    """
    Returns a moderation decision dict.
    Falls back to a safe 'no violation' dict on any error.
    """
    system = MODERATION_SYSTEM.replace("{custom_rules}", custom_rules)
    raw = await ask(system, message_content, temperature=0.0, max_tokens=256)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Moderation JSON parse failed, raw: %s", raw[:200])
        return {
            "violation": False,
            "categories": [],
            "severity": "low",
            "action": "none",
            "reason": "parse error",
        }


# ── Server builder planner ─────────────────────────────────────────────────────

SERVER_BUILDER_SYSTEM = """
You are an expert Discord server architect. The admin will give you a one-sentence description
of the kind of server they want to build. Convert it into a structured build plan.

Respond ONLY with a valid JSON object — no markdown, no extra text.

Schema:
{
  "server_name": "string",
  "description": "string",
  "categories": [
    {
      "name": "string",
      "channels": [
        {
          "name": "string",
          "type": "text" | "voice" | "announcement" | "forum",
          "topic": "string",
          "slowmode": 0,
          "nsfw": false
        }
      ]
    }
  ],
  "roles": [
    {
      "name": "string",
      "color": "#hexcode",
      "permissions": ["send_messages","read_messages","manage_messages","kick_members","ban_members","administrator"],
      "hoisted": false,
      "mentionable": false
    }
  ],
  "rules": ["string"],
  "welcome_message": "string",
  "verification_channel": "string | null"
}
"""


async def build_server_plan(instruction: str) -> dict[str, Any] | None:
    """
    Convert a free-text server description into a structured build plan.
    Returns None on failure.
    """
    raw = await ask(SERVER_BUILDER_SYSTEM, instruction, temperature=0.4, max_tokens=2048)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Server plan JSON parse failed, raw: %s", raw[:300])
        return None


# ── General admin instruction handler ─────────────────────────────────────────

ADMIN_SYSTEM = """
You are Klaud, an AI-powered Discord server assistant. You help server admins manage, moderate,
and improve their servers. Be concise, helpful, and professional.
Always prefix dangerous or irreversible actions with ⚠️ and ask for confirmation.
"""


async def admin_query(instruction: str, context: str = "") -> str:
    """Handle a freeform admin question or instruction. Returns a plain text reply."""
    user_msg = instruction
    if context:
        user_msg = f"Context:\n{context}\n\nInstruction:\n{instruction}"
    return await ask(ADMIN_SYSTEM, user_msg, temperature=0.5)
