"""
KLAUD-NINJA — Gemini AI Service Layer
Isolates all Google Gemini API interactions.
Provides structured outputs, retry logic, timeout handling,
and a clean interface that can be swapped for another provider.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("klaud.gemini")

try:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False
    logger.warning("google-generativeai not installed — AI features disabled")


# ─── Output Models ───────────────────────────────────────────────────────────

class ModerationAction(str, Enum):
    NONE = "none"
    WARN = "warn"
    DELETE = "delete"
    TIMEOUT = "timeout"
    KICK = "kick"
    BAN = "ban"


@dataclass
class ModerationDecision:
    """Structured output from Gemini moderation analysis."""

    action: ModerationAction = ModerationAction.NONE
    confidence: float = 0.0
    categories: list[str] = field(default_factory=list)
    reason: str = ""
    timeout_duration: int = 600           # seconds, if action == TIMEOUT
    delete_message: bool = False          # always True if action == DELETE+
    ai_generated: bool = True

    @classmethod
    def safe_default(cls) -> "ModerationDecision":
        """Returned when AI is unavailable — take no action."""
        return cls(action=ModerationAction.NONE, ai_generated=False, reason="AI unavailable")

    @classmethod
    def from_dict(cls, data: dict) -> "ModerationDecision":
        action_str = data.get("action", "none").lower()
        try:
            action = ModerationAction(action_str)
        except ValueError:
            action = ModerationAction.NONE

        return cls(
            action=action,
            confidence=float(data.get("confidence", 0.0)),
            categories=data.get("categories", []),
            reason=data.get("reason", ""),
            timeout_duration=int(data.get("timeout_duration", 600)),
            delete_message=action not in (ModerationAction.NONE, ModerationAction.WARN),
        )


@dataclass
class AdminCommandDecision:
    """Structured output from Gemini admin intent parsing."""

    action_type: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    confirmation_required: bool = False
    explanation: str = ""
    valid: bool = False

    @classmethod
    def invalid(cls, reason: str) -> "AdminCommandDecision":
        return cls(valid=False, explanation=reason)

    @classmethod
    def from_dict(cls, data: dict) -> "AdminCommandDecision":
        risky_actions = {
            "delete_channel", "kick", "ban", "set_permissions", "delete_category"
        }
        action = data.get("action_type", "")
        return cls(
            action_type=action,
            parameters=data.get("parameters", {}),
            confirmation_required=action in risky_actions or data.get("confirmation_required", False),
            explanation=data.get("explanation", ""),
            valid=bool(action),
        )


# ─── Prompts ─────────────────────────────────────────────────────────────────

_MODERATION_SYSTEM = """
You are KLAUD, a strict Discord moderation AI. Analyze the given message and respond ONLY with valid JSON.

Detectable violation categories:
- toxicity
- harassment
- spam
- scam
- nsfw_text
- threat
- hate_speech
- profanity
- caps_abuse
- invite_link
- raiding

Intensity levels and their enforcement thresholds:
- LOW: Only act on extreme violations (hate speech, threats, scams). Ignore mild language.
- MEDIUM: Act on clear toxicity, harassment, spam, scams. Allow minor profanity.
- HIGH: Act on profanity, caps abuse, invite links, any toxicity. Strict enforcement.
- EXTREME: Zero tolerance. Act on anything suspicious including borderline content.

Available actions (in escalation order): none, warn, delete, timeout, kick, ban

Response format (JSON only, no markdown, no explanation):
{
  "action": "none|warn|delete|timeout|kick|ban",
  "confidence": 0.0-1.0,
  "categories": ["category1", "category2"],
  "reason": "Short explanation for the moderation action",
  "timeout_duration": 600
}
"""

_ADMIN_SYSTEM = """
You are KLAUD, a Discord server management AI. An admin has given you an instruction.
Interpret their intent and respond ONLY with valid JSON describing what action to take.

Supported action types:
- create_category: {"name": str}
- create_channel: {"name": str, "category": str|null, "type": "text"|"voice", "topic": str|null}
- delete_channel: {"channel_name": str}
- rename_channel: {"old_name": str, "new_name": str}
- set_permissions: {"channel_name": str, "role_name": str, "allow": [...], "deny": [...]}
- create_role: {"name": str, "color": str|null, "permissions": [...]}
- assign_role: {"role_name": str, "user_mention": str}
- lock_channel: {"channel_name": str}
- unlock_channel: {"channel_name": str}
- setup_verification: {"channel_name": str, "role_name": str}
- setup_basic_server: {}

For bulk channel creation, use action_type "bulk_create_channels" with:
{"channels": [{"name": str, "category": str, "type": "text"}]}

Response format (JSON only, no markdown, no preamble):
{
  "action_type": "action_name",
  "parameters": { ... },
  "explanation": "Brief human-readable summary",
  "confirmation_required": false
}
"""


# ─── Service ─────────────────────────────────────────────────────────────────

class GeminiService:
    """
    Async wrapper around the Google Gemini API.
    Provides retry logic, timeout enforcement, and structured output parsing.
    All methods return typed dataclasses, never raw strings.
    """

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-1.5-flash",
        timeout: float = 15.0,
        max_retries: int = 3,
    ) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._timeout = timeout
        self._max_retries = max_retries
        self._model: Any = None
        self._available = False

        self._call_count = 0
        self._error_count = 0
        self._last_error: Optional[str] = None

    async def initialise(self) -> None:
        """Set up the Gemini client. Call once at bot startup."""
        if not HAS_GEMINI:
            logger.warning("Gemini library not available — AI disabled")
            return

        if not self._api_key:
            logger.warning("AI_API_KEY not set — AI features disabled")
            return

        try:
            genai.configure(api_key=self._api_key)
            self._model = genai.GenerativeModel(
                model_name=self._model_name,
                generation_config=GenerationConfig(
                    temperature=0.1,    # Low temperature = more deterministic
                    max_output_tokens=1024,
                    response_mime_type="application/json",
                ),
            )
            # Quick connectivity test
            await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._model.generate_content("ping")
                ),
                timeout=10.0,
            )
            self._available = True
            logger.info(f"Gemini AI initialised | model={self._model_name}")
        except asyncio.TimeoutError:
            logger.warning("Gemini connectivity test timed out — running in degraded mode")
        except Exception as e:
            logger.warning(f"Gemini init failed: {e} — running in degraded mode")

    @property
    def available(self) -> bool:
        return self._available and self._model is not None

    # ─── Moderation ───────────────────────────────────────────────────────────

    async def analyze_message(
        self,
        content: str,
        intensity: str = "MEDIUM",
        author_info: Optional[str] = None,
        channel_info: Optional[str] = None,
    ) -> ModerationDecision:
        """
        Analyze a message for policy violations.
        Returns a ModerationDecision with action and confidence.
        Falls back to rule-based analysis if Gemini is unavailable.
        """
        if not self.available:
            return self._fallback_moderate(content, intensity)

        context_parts = [
            f"Intensity: {intensity}",
            f"Message: {content!r}",
        ]
        if author_info:
            context_parts.append(f"Author: {author_info}")
        if channel_info:
            context_parts.append(f"Channel: {channel_info}")

        prompt = "\n".join(context_parts)

        raw = await self._call_with_retry(
            system=_MODERATION_SYSTEM,
            prompt=prompt,
            operation="moderation",
        )

        if raw is None:
            return self._fallback_moderate(content, intensity)

        try:
            data = self._extract_json(raw)
            return ModerationDecision.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to parse moderation response: {e} | raw={raw[:200]}")
            return self._fallback_moderate(content, intensity)

    # ─── Admin AI ─────────────────────────────────────────────────────────────

    async def parse_admin_command(
        self,
        instruction: str,
        guild_context: Optional[str] = None,
    ) -> AdminCommandDecision:
        """
        Parse a natural language admin instruction into a structured action.
        Returns AdminCommandDecision.invalid() if parsing fails.
        """
        if not self.available:
            return AdminCommandDecision.invalid("AI service is currently unavailable.")

        context = instruction
        if guild_context:
            context = f"Server context: {guild_context}\nInstruction: {instruction}"

        raw = await self._call_with_retry(
            system=_ADMIN_SYSTEM,
            prompt=context,
            operation="admin_command",
        )

        if raw is None:
            return AdminCommandDecision.invalid("AI did not respond. Try again in a moment.")

        try:
            data = self._extract_json(raw)
            return AdminCommandDecision.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to parse admin command response: {e} | raw={raw[:200]}")
            return AdminCommandDecision.invalid("I couldn't understand that instruction. Please rephrase it.")

    # ─── Free-form query ─────────────────────────────────────────────────────

    async def ask(self, prompt: str, system: Optional[str] = None) -> Optional[str]:
        """
        Free-form Gemini query. Returns the raw text response or None on failure.
        For internal use — prefer typed methods above.
        """
        return await self._call_with_retry(
            system=system or "You are KLAUD, a helpful Discord bot assistant.",
            prompt=prompt,
            operation="ask",
        )

    # ─── Core caller ─────────────────────────────────────────────────────────

    async def _call_with_retry(
        self,
        system: str,
        prompt: str,
        operation: str,
    ) -> Optional[str]:
        """
        Call Gemini with exponential backoff retry.
        Returns the text content or None if all retries fail.
        """
        full_prompt = f"{system}\n\n---\n\n{prompt}"
        attempt = 0
        delay = 1.0

        while attempt < self._max_retries:
            attempt += 1
            try:
                self._call_count += 1
                result = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self._model.generate_content(full_prompt),
                    ),
                    timeout=self._timeout,
                )

                if result and result.text:
                    return result.text.strip()

                logger.warning(f"Gemini returned empty response on {operation} (attempt {attempt})")

            except asyncio.TimeoutError:
                self._error_count += 1
                self._last_error = "timeout"
                logger.warning(
                    f"Gemini timeout on {operation} (attempt {attempt}/{self._max_retries})"
                )

            except Exception as e:
                self._error_count += 1
                self._last_error = str(e)
                logger.error(
                    f"Gemini error on {operation} (attempt {attempt}/{self._max_retries}): {e}"
                )

            if attempt < self._max_retries:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10.0)  # Exponential backoff, capped at 10s

        logger.error(f"Gemini failed after {self._max_retries} attempts for {operation}")
        return None

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _extract_json(self, text: str) -> dict:
        """
        Extract and parse a JSON object from Gemini's response.
        Handles cases where Gemini wraps output in markdown code fences.
        """
        text = text.strip()

        # Strip markdown fences if present
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            text = text.strip()

        # Find first {...} block
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            text = text[start:end]

        return json.loads(text)

    def _fallback_moderate(self, content: str, intensity: str) -> ModerationDecision:
        """
        Rule-based moderation fallback when Gemini is unavailable.
        Catches the most obvious violations using simple heuristics.
        """
        from services.ai_fallback import FallbackModerator
        return FallbackModerator.analyze(content, intensity)

    def stats(self) -> dict:
        """Return service health stats."""
        return {
            "available": self.available,
            "model": self._model_name,
            "total_calls": self._call_count,
            "total_errors": self._error_count,
            "error_rate": round(self._error_count / max(self._call_count, 1), 3),
            "last_error": self._last_error,
        }
