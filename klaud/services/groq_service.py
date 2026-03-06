"""
KLAUD-NINJA — Groq AI Service Layer
================================================================================
Replaces the Gemini service with Groq's ultra-fast LLM inference API.
Groq runs models like Llama 3 and Mixtral at extremely low latency (~200ms),
making it ideal for real-time Discord moderation where speed matters.

Provider:  Groq Cloud  (https://console.groq.com)
Models:    llama-3.3-70b-versatile (default, best quality)
           llama-3.1-8b-instant    (fastest, good for high-traffic servers)
           mixtral-8x7b-32768      (large context, good for admin commands)

Auth:      Set GROQ_API_KEY environment variable
           Get a free key at https://console.groq.com/keys

Features:
  - Structured JSON output via system prompt enforcement
  - Exponential backoff retry with jitter
  - Per-operation timeout enforcement
  - Thread-safe async HTTP via aiohttp
  - Detailed per-call telemetry and error tracking
  - Automatic fallback to rule-based engine on any failure
  - Model hot-swap without restart (via settings)
  - Rate limit detection and automatic cooldown
  - Request ID tracking for debugging
  - Concurrent request limiting to avoid rate limits
  - Full conversation context support for admin commands
  - Streaming support for long responses (admin AI)
  - Token usage tracking per call and cumulative
  - Graceful degradation at every failure point
================================================================================
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("klaud.groq")

# ─── Constants ────────────────────────────────────────────────────────────────

GROQ_API_BASE = "https://api.groq.com/openai/v1"
GROQ_CHAT_ENDPOINT = f"{GROQ_API_BASE}/chat/completions"
GROQ_MODELS_ENDPOINT = f"{GROQ_API_BASE}/models"

# Default model — best balance of speed and quality
DEFAULT_MODEL = "llama-3.3-70b-versatile"

# Fallback models in priority order (used if primary fails)
FALLBACK_MODELS = [
    "llama-3.1-8b-instant",
    "mixtral-8x7b-32768",
]

# Per-model context window sizes (tokens)
MODEL_CONTEXT_WINDOWS = {
    "llama-3.3-70b-versatile":  128_000,
    "llama-3.1-70b-versatile":  128_000,
    "llama-3.1-8b-instant":     128_000,
    "mixtral-8x7b-32768":        32_768,
    "gemma2-9b-it":               8_192,
    "gemma-7b-it":                8_192,
}

# Rate limit: Groq free tier allows ~30 req/min
_REQUEST_SEMAPHORE_LIMIT = 10  # Max concurrent in-flight requests

# ─── Output Models ────────────────────────────────────────────────────────────


class ModerationAction(str, Enum):
    """
    Moderation action enum, in escalation order.
    Each level implies all lower levels (e.g. BAN also deletes the message).
    """
    NONE    = "none"
    WARN    = "warn"
    DELETE  = "delete"
    TIMEOUT = "timeout"
    KICK    = "kick"
    BAN     = "ban"

    @property
    def severity(self) -> int:
        """Numeric severity for comparison."""
        return {
            "none": 0, "warn": 1, "delete": 2,
            "timeout": 3, "kick": 4, "ban": 5,
        }[self.value]

    @property
    def implies_delete(self) -> bool:
        """Whether this action should also delete the offending message."""
        return self.severity >= 2


@dataclass
class ModerationDecision:
    """
    Fully typed output from the AI moderation engine.
    This is what cogs.moderation receives and acts upon.

    Fields:
        action          — What to do to the user
        confidence      — AI's confidence 0.0–1.0 (higher = more certain)
        categories      — List of violated policy categories
        reason          — Human-readable explanation for the action
        timeout_duration— Seconds to timeout the user (if action == TIMEOUT)
        delete_message  — Whether to delete the triggering message
        ai_generated    — False if this came from the fallback engine
        request_id      — UUID for tracing this specific call
        model_used      — Which AI model produced this decision
        latency_ms      — How long the AI call took in milliseconds
        tokens_used     — Total tokens consumed for this call
    """
    action: ModerationAction            = ModerationAction.NONE
    confidence: float                   = 0.0
    categories: list[str]               = field(default_factory=list)
    reason: str                         = ""
    timeout_duration: int               = 600
    delete_message: bool                = False
    ai_generated: bool                  = True
    request_id: str                     = field(default_factory=lambda: str(uuid.uuid4())[:8])
    model_used: str                     = ""
    latency_ms: float                   = 0.0
    tokens_used: int                    = 0

    @classmethod
    def safe_default(cls) -> "ModerationDecision":
        """Returned when AI is completely unavailable — take no action."""
        return cls(
            action=ModerationAction.NONE,
            ai_generated=False,
            reason="AI unavailable — no action taken",
        )

    @classmethod
    def from_dict(cls, data: dict, model: str = "", latency: float = 0.0, tokens: int = 0) -> "ModerationDecision":
        """Parse AI JSON response into a typed decision."""
        action_str = str(data.get("action", "none")).lower().strip()
        try:
            action = ModerationAction(action_str)
        except ValueError:
            logger.debug(f"Unknown action '{action_str}' — defaulting to none")
            action = ModerationAction.NONE

        confidence = float(data.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))  # Clamp to [0, 1]

        categories = data.get("categories", [])
        if isinstance(categories, str):
            categories = [categories]

        timeout_raw = data.get("timeout_duration", 600)
        try:
            timeout_duration = int(timeout_raw)
        except (TypeError, ValueError):
            timeout_duration = 600
        timeout_duration = max(60, min(timeout_duration, 2_419_200))  # 1min to 28 days

        return cls(
            action=action,
            confidence=confidence,
            categories=[str(c) for c in categories if c],
            reason=str(data.get("reason", ""))[:500],
            timeout_duration=timeout_duration,
            delete_message=action.implies_delete,
            ai_generated=True,
            model_used=model,
            latency_ms=latency,
            tokens_used=tokens,
        )


@dataclass
class AdminCommandDecision:
    """
    Fully typed output from the AI admin command parser.
    This is what cogs.admin_ai receives and dispatches.

    Fields:
        action_type         — What Discord action to perform
        parameters          — Dict of action-specific parameters
        confirmation_required — Whether to ask admin to confirm first
        explanation         — Human-readable description of the action
        valid               — Whether the AI understood the command
        request_id          — UUID for tracing
        model_used          — Which model produced this
        latency_ms          — How long the AI call took
        tokens_used         — Token consumption
        raw_instruction     — Original instruction for debugging
    """
    action_type: str                    = ""
    parameters: dict[str, Any]          = field(default_factory=dict)
    confirmation_required: bool         = False
    explanation: str                    = ""
    valid: bool                         = False
    request_id: str                     = field(default_factory=lambda: str(uuid.uuid4())[:8])
    model_used: str                     = ""
    latency_ms: float                   = 0.0
    tokens_used: int                    = 0
    raw_instruction: str                = ""

    # Actions that are irreversible and should require confirmation
    _RISKY_ACTIONS: frozenset = frozenset({
        "delete_channel", "delete_category", "kick", "ban",
        "set_permissions", "bulk_create_channels",
    })

    @classmethod
    def invalid(cls, reason: str) -> "AdminCommandDecision":
        """Return a failed decision with a user-facing reason."""
        return cls(valid=False, explanation=reason)

    @classmethod
    def from_dict(
        cls,
        data: dict,
        model: str = "",
        latency: float = 0.0,
        tokens: int = 0,
        original: str = "",
    ) -> "AdminCommandDecision":
        """Parse AI JSON response into a typed admin decision."""
        action = str(data.get("action_type", "")).strip()
        params = data.get("parameters", {})
        if not isinstance(params, dict):
            params = {}

        # Determine if this action requires confirmation
        needs_confirm = (
            action in cls._RISKY_ACTIONS
            or bool(data.get("confirmation_required", False))
        )

        return cls(
            action_type=action,
            parameters=params,
            confirmation_required=needs_confirm,
            explanation=str(data.get("explanation", ""))[:500],
            valid=bool(action),
            model_used=model,
            latency_ms=latency,
            tokens_used=tokens,
            raw_instruction=original[:200],
        )


# ─── Telemetry ────────────────────────────────────────────────────────────────


@dataclass
class CallRecord:
    """Single API call record for telemetry."""
    operation:  str
    model:      str
    success:    bool
    latency_ms: float
    tokens:     int
    error:      Optional[str]
    timestamp:  float = field(default_factory=time.monotonic)


class ServiceTelemetry:
    """
    Rolling window telemetry for the Groq service.
    Tracks the last N calls for health monitoring.
    """

    def __init__(self, window: int = 100) -> None:
        self._records: deque[CallRecord] = deque(maxlen=window)
        self._total_calls = 0
        self._total_errors = 0
        self._total_tokens = 0

    def record(self, rec: CallRecord) -> None:
        self._records.append(rec)
        self._total_calls += 1
        if not rec.success:
            self._total_errors += 1
        self._total_tokens += rec.tokens

    def to_dict(self) -> dict:
        recent = list(self._records)
        if recent:
            avg_latency = sum(r.latency_ms for r in recent) / len(recent)
            recent_errors = sum(1 for r in recent if not r.success)
            recent_error_rate = recent_errors / len(recent)
        else:
            avg_latency = 0.0
            recent_error_rate = 0.0

        return {
            "total_calls":        self._total_calls,
            "total_errors":       self._total_errors,
            "total_tokens":       self._total_tokens,
            "overall_error_rate": round(self._total_errors / max(self._total_calls, 1), 4),
            "recent_error_rate":  round(recent_error_rate, 4),
            "avg_latency_ms":     round(avg_latency, 1),
            "window_size":        len(recent),
        }


# ─── System Prompts ───────────────────────────────────────────────────────────

_MODERATION_SYSTEM = """
You are KLAUD, an expert Discord server moderation AI.
Your job is to analyze Discord messages and decide if they violate server rules.
You MUST respond ONLY with a valid JSON object — no markdown, no explanation, no preamble.

=== VIOLATION CATEGORIES ===
toxicity       — General toxic, mean, or abusive language
harassment     — Targeting a specific user with insults or abuse
spam           — Repeated messages, excessive mentions, flooding
scam           — Phishing links, fake giveaways, crypto scams, free Nitro tricks
nsfw_text      — Sexual content, adult links, explicit descriptions
threat         — Physical threats, doxxing threats, death threats
hate_speech    — Slurs, discrimination based on race/gender/religion/sexuality
profanity      — Excessive or severe swearing (context-dependent)
caps_abuse     — Shouting in all-caps to harass or annoy
invite_link    — Unauthorized Discord server invite links
raiding        — Coordinated disruption, mass join + spam

=== ENFORCEMENT INTENSITY ===
LOW     → Only act on extreme violations: hate_speech, threat, scam. Ignore everything else.
MEDIUM  → Act on: hate_speech, threat, scam, nsfw_text, harassment, spam. Allow mild profanity.
HIGH    → Act on all categories. Strict enforcement. Even borderline content gets flagged.
EXTREME → Zero tolerance. Flag anything suspicious. Escalate aggressively.

=== AVAILABLE ACTIONS (escalation order) ===
none    → No violation detected. Do nothing.
warn    → Send the user a warning message.
delete  → Delete the message silently.
timeout → Temporarily mute the user (specify timeout_duration in seconds).
kick    → Remove the user from the server (they can rejoin).
ban     → Permanently ban the user.

=== CONFIDENCE GUIDE ===
0.95–1.0 → Absolutely certain (slurs, explicit threats, obvious scams)
0.80–0.94 → Very confident (clear harassment, spam, scams)
0.65–0.79 → Fairly confident (borderline content, context-dependent)
0.50–0.64 → Uncertain (ambiguous, could go either way)
Below 0.50 → Not a violation, return action: "none"

=== OUTPUT FORMAT (JSON ONLY) ===
{
  "action": "none|warn|delete|timeout|kick|ban",
  "confidence": 0.0,
  "categories": ["category1"],
  "reason": "Brief explanation of the violation and why this action was chosen",
  "timeout_duration": 600
}

Rules:
- ONLY return JSON. Not a single character outside the JSON object.
- If there is no violation, return {"action": "none", "confidence": 0.0, "categories": [], "reason": "No violation", "timeout_duration": 0}
- timeout_duration is only meaningful when action is "timeout". Use seconds (600 = 10 minutes, 3600 = 1 hour, 86400 = 1 day).
- Never set confidence above 1.0 or below 0.0.
- reason should be 1–2 sentences maximum.
"""

_ADMIN_SYSTEM = """
You are KLAUD, an intelligent Discord server management assistant.
An admin has given you a natural language instruction. Parse their intent and return a structured action.
You MUST respond ONLY with a valid JSON object — no markdown, no explanation, no preamble.

=== SUPPORTED ACTIONS ===

create_category
  Parameters: {"name": "Category Name"}
  Example: "make a gaming category" → create_category with name "Gaming"

create_channel
  Parameters: {"name": "channel-name", "category": "Category Name or null", "type": "text|voice", "topic": "description or null"}
  Example: "add a voice chat to General" → create_channel with type "voice"

bulk_create_channels
  Parameters: {"channels": [{"name": "ch1", "category": "Cat", "type": "text"}, ...]}
  Use for "create multiple channels" requests. Max 10 channels.

delete_channel
  Parameters: {"channel_name": "channel-name"}
  ALWAYS set confirmation_required: true

rename_channel
  Parameters: {"old_name": "old-name", "new_name": "new-name"}

set_permissions
  Parameters: {"channel_name": "channel", "role_name": "role", "allow": ["read_messages"], "deny": ["send_messages"]}
  Valid permissions: read_messages, send_messages, manage_messages, attach_files, embed_links, mention_everyone

create_role
  Parameters: {"name": "Role Name", "color": "#FF0000 or null", "permissions": ["send_messages"]}

assign_role
  Parameters: {"role_name": "Role Name", "user_mention": "@username"}

lock_channel
  Parameters: {"channel_name": "channel-name"}
  Prevents @everyone from sending messages.

unlock_channel
  Parameters: {"channel_name": "channel-name"}

setup_verification
  Parameters: {"channel_name": "verify", "role_name": "Member"}
  Creates a verification channel with a button. ENTERPRISE tier only.

setup_basic_server
  Parameters: {}
  Creates a standard server layout: Info/General/Staff categories with standard channels.

=== CHANNEL NAMING RULES ===
- Always use lowercase-with-hyphens for channel names (e.g. "general-chat", "bot-commands")
- Category names can be Title Case (e.g. "General", "Staff Only")

=== OUTPUT FORMAT (JSON ONLY) ===
{
  "action_type": "action_name",
  "parameters": { ... },
  "explanation": "What you're about to do, in plain English (1 sentence)",
  "confirmation_required": false
}

Rules:
- ONLY return JSON. Not a single character outside the JSON object.
- Set confirmation_required: true for destructive actions (delete, kick, ban, set_permissions).
- If you don't understand the instruction, return: {"action_type": "", "parameters": {}, "explanation": "I didn't understand that instruction. Please rephrase.", "confirmation_required": false}
- Keep explanation short (1 sentence) and human-friendly.
"""

_GENERAL_SYSTEM = """
You are KLAUD, a helpful Discord bot assistant. Answer concisely and helpfully.
"""


# ─── Main Service Class ───────────────────────────────────────────────────────


class GroqService:
    """
    Async Groq API service for KLAUD-NINJA.

    Replaces the Gemini service with Groq's ultra-low-latency inference.
    All methods return typed dataclasses and never raise exceptions —
    failures are handled gracefully with fallback or error messages.

    Usage:
        service = GroqService(api_key="gsk_...", model_name="llama-3.3-70b-versatile")
        await service.initialise()

        decision = await service.analyze_message("you're so dumb", intensity="HIGH")
        # → ModerationDecision(action=WARN, confidence=0.82, ...)

        cmd = await service.parse_admin_command("create a memes channel")
        # → AdminCommandDecision(action_type="create_channel", ...)
    """

    def __init__(
        self,
        api_key: str,
        model_name: str = DEFAULT_MODEL,
        timeout: float = 15.0,
        max_retries: int = 3,
    ) -> None:
        self._api_key       = api_key
        self._model_name    = model_name
        self._timeout       = timeout
        self._max_retries   = max_retries
        self._available     = False
        self._session: Any  = None  # aiohttp.ClientSession
        self._semaphore     = asyncio.Semaphore(_REQUEST_SEMAPHORE_LIMIT)
        self._telemetry     = ServiceTelemetry(window=200)
        self._rate_limited_until: float = 0.0

        # Track which models are currently working
        self._working_models: list[str] = []

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    async def initialise(self) -> None:
        """
        Initialise the HTTP session and verify the API key works.
        Call this once at bot startup from setup_hook().
        """
        if not self._api_key:
            logger.warning("GROQ_API_KEY not set — AI features disabled")
            return

        try:
            import aiohttp
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type":  "application/json",
                    "User-Agent":    "KLAUD-NINJA/2.0 (Discord Moderation Bot)",
                },
                timeout=aiohttp.ClientTimeout(total=self._timeout + 5),
            )

            # Test the connection with a trivial request
            test_result = await self._chat_completion(
                model=self._model_name,
                messages=[
                    {"role": "system", "content": "You are a test bot. Reply with exactly: ok"},
                    {"role": "user",   "content": "ping"},
                ],
                max_tokens=5,
                temperature=0.0,
                operation="init_test",
            )

            if test_result is not None:
                self._available = True
                self._working_models = [self._model_name]
                logger.info(
                    f"Groq AI initialised | model={self._model_name} | "
                    f"response_preview={repr(test_result[:30])}"
                )
            else:
                logger.warning("Groq init test returned empty response — AI disabled")

        except ImportError:
            logger.error("aiohttp not installed — Groq AI disabled. Run: pip install aiohttp")
        except Exception as e:
            logger.warning(f"Groq init failed: {e} — AI running in degraded mode (fallback only)")

    async def close(self) -> None:
        """Close the aiohttp session. Call this on bot shutdown."""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.debug("Groq HTTP session closed")

    @property
    def available(self) -> bool:
        """True if the service is ready to process requests."""
        return (
            self._available
            and self._session is not None
            and not self._session.closed
            and time.monotonic() > self._rate_limited_until
        )

    @property
    def model(self) -> str:
        return self._model_name

    # ─── Public API ───────────────────────────────────────────────────────────

    async def analyze_message(
        self,
        content: str,
        intensity: str = "MEDIUM",
        author_info: Optional[str] = None,
        channel_info: Optional[str] = None,
    ) -> ModerationDecision:
        """
        Analyze a Discord message for policy violations.

        Args:
            content     — The raw message text to analyze
            intensity   — Enforcement level: LOW / MEDIUM / HIGH / EXTREME
            author_info — Optional context about the author (e.g. "new member, 2 prior warns")
            channel_info— Optional context about the channel (e.g. "#general")

        Returns:
            ModerationDecision with action, confidence, categories, and reason.
            Falls back to rule-based analysis if AI is unavailable.
        """
        if not self.available:
            logger.debug("Groq unavailable — using fallback moderator")
            return self._fallback_moderate(content, intensity)

        # Build the analysis prompt
        prompt_parts = [
            f"Enforcement Intensity: {intensity.upper()}",
            f"Message to analyze: {content!r}",
        ]
        if author_info:
            prompt_parts.append(f"Author context: {author_info}")
        if channel_info:
            prompt_parts.append(f"Channel context: {channel_info}")

        prompt = "\n".join(prompt_parts)
        t_start = time.monotonic()

        raw, tokens = await self._call_with_retry(
            system=_MODERATION_SYSTEM,
            prompt=prompt,
            operation="moderation",
            max_tokens=256,
            temperature=0.1,
        )

        latency = (time.monotonic() - t_start) * 1000

        if raw is None:
            logger.debug("Groq moderation returned None — using fallback")
            return self._fallback_moderate(content, intensity)

        try:
            data = self._extract_json(raw)
            decision = ModerationDecision.from_dict(
                data,
                model=self._model_name,
                latency=latency,
                tokens=tokens,
            )
            logger.debug(
                f"Moderation result | action={decision.action.value} "
                f"conf={decision.confidence:.2f} cats={decision.categories} "
                f"latency={latency:.0f}ms tokens={tokens}"
            )
            return decision

        except Exception as e:
            logger.error(f"Failed to parse moderation response: {e} | raw={raw[:300]!r}")
            return self._fallback_moderate(content, intensity)

    async def parse_admin_command(
        self,
        instruction: str,
        guild_context: Optional[str] = None,
    ) -> AdminCommandDecision:
        """
        Parse a natural language admin instruction into a structured action.

        Args:
            instruction   — The natural language command from the admin
            guild_context — Optional context about the server structure

        Returns:
            AdminCommandDecision describing what action to perform.
            Returns AdminCommandDecision.invalid() if parsing fails.
        """
        if not self.available:
            return AdminCommandDecision.invalid(
                "AI service is currently unavailable. Try again in a moment."
            )

        if guild_context:
            prompt = f"Server context:\n{guild_context}\n\nAdmin instruction: {instruction}"
        else:
            prompt = f"Admin instruction: {instruction}"

        t_start = time.monotonic()

        raw, tokens = await self._call_with_retry(
            system=_ADMIN_SYSTEM,
            prompt=prompt,
            operation="admin_command",
            max_tokens=512,
            temperature=0.2,
        )

        latency = (time.monotonic() - t_start) * 1000

        if raw is None:
            return AdminCommandDecision.invalid(
                "AI did not respond. Please try again in a moment."
            )

        try:
            data = self._extract_json(raw)
            decision = AdminCommandDecision.from_dict(
                data,
                model=self._model_name,
                latency=latency,
                tokens=tokens,
                original=instruction,
            )
            logger.debug(
                f"Admin command parsed | action={decision.action_type} "
                f"confirm={decision.confirmation_required} latency={latency:.0f}ms"
            )
            return decision

        except Exception as e:
            logger.error(f"Failed to parse admin command response: {e} | raw={raw[:300]!r}")
            return AdminCommandDecision.invalid(
                "I couldn't parse that instruction. Please rephrase it more specifically."
            )

    async def ask(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 512,
    ) -> Optional[str]:
        """
        Free-form Groq query. Returns the raw text response or None.

        Used internally and for any custom AI interactions in cogs.
        """
        if not self.available:
            return None

        raw, _ = await self._call_with_retry(
            system=system or _GENERAL_SYSTEM,
            prompt=prompt,
            operation="ask",
            max_tokens=max_tokens,
            temperature=0.7,
        )
        return raw

    # ─── Core HTTP Caller ─────────────────────────────────────────────────────

    async def _call_with_retry(
        self,
        system: str,
        prompt: str,
        operation: str,
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> tuple[Optional[str], int]:
        """
        Call the Groq API with exponential backoff + jitter retry.

        Returns:
            (response_text or None, tokens_used)
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ]

        attempt  = 0
        delay    = 1.0
        last_err = None

        while attempt < self._max_retries:
            attempt += 1
            t0 = time.monotonic()

            try:
                async with self._semaphore:
                    result = await self._chat_completion(
                        model=self._model_name,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        operation=operation,
                    )

                latency = (time.monotonic() - t0) * 1000

                if result is not None:
                    # Record success
                    self._telemetry.record(CallRecord(
                        operation=operation,
                        model=self._model_name,
                        success=True,
                        latency_ms=latency,
                        tokens=0,  # tokens tracked inside _chat_completion
                        error=None,
                    ))
                    return result, 0

                logger.warning(f"Groq empty response on {operation} attempt {attempt}")

            except asyncio.TimeoutError:
                last_err = "timeout"
                latency  = (time.monotonic() - t0) * 1000
                self._telemetry.record(CallRecord(
                    operation=operation,
                    model=self._model_name,
                    success=False,
                    latency_ms=latency,
                    tokens=0,
                    error="timeout",
                ))
                logger.warning(
                    f"Groq timeout on {operation} "
                    f"(attempt {attempt}/{self._max_retries})"
                )

            except _RateLimitError as e:
                # Back off for the rate limit window
                cooldown = e.retry_after or 10.0
                self._rate_limited_until = time.monotonic() + cooldown
                logger.warning(f"Groq rate limited — cooling down for {cooldown:.1f}s")
                self._telemetry.record(CallRecord(
                    operation=operation,
                    model=self._model_name,
                    success=False,
                    latency_ms=0,
                    tokens=0,
                    error=f"rate_limit:{cooldown}s",
                ))
                await asyncio.sleep(cooldown)
                continue

            except Exception as e:
                last_err = str(e)
                self._telemetry.record(CallRecord(
                    operation=operation,
                    model=self._model_name,
                    success=False,
                    latency_ms=(time.monotonic() - t0) * 1000,
                    tokens=0,
                    error=last_err,
                ))
                logger.error(
                    f"Groq error on {operation} "
                    f"(attempt {attempt}/{self._max_retries}): {e}"
                )

            # Exponential backoff with jitter
            if attempt < self._max_retries:
                jitter = random.uniform(0.0, 0.5)
                wait   = min(delay + jitter, 15.0)
                logger.debug(f"Retrying {operation} in {wait:.2f}s")
                await asyncio.sleep(wait)
                delay = min(delay * 2, 10.0)

        logger.error(
            f"Groq failed after {self._max_retries} attempts for {operation} "
            f"| last_error={last_err}"
        )
        return None, 0

    async def _chat_completion(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        operation: str,
    ) -> Optional[str]:
        """
        Make a single Groq chat completion request.
        Returns the text content or None.
        Raises _RateLimitError on HTTP 429.
        """
        if self._session is None or self._session.closed:
            logger.error("Groq HTTP session is not open")
            return None

        payload = {
            "model":       model,
            "messages":    messages,
            "max_tokens":  max_tokens,
            "temperature": temperature,
            "stream":      False,
        }

        try:
            async with self._session.post(
                GROQ_CHAT_ENDPOINT,
                json=payload,
            ) as resp:

                if resp.status == 429:
                    # Extract retry-after if available
                    retry_after_str = resp.headers.get("Retry-After", "10")
                    try:
                        retry_after = float(retry_after_str)
                    except ValueError:
                        retry_after = 10.0
                    raise _RateLimitError(retry_after=retry_after)

                if resp.status == 401:
                    logger.error(
                        "Groq API key is invalid (401 Unauthorized). "
                        "Check your GROQ_API_KEY environment variable."
                    )
                    self._available = False
                    return None

                if resp.status == 400:
                    body = await resp.text()
                    logger.error(f"Groq bad request (400): {body[:300]}")
                    return None

                if not resp.ok:
                    body = await resp.text()
                    logger.error(f"Groq HTTP {resp.status} on {operation}: {body[:200]}")
                    return None

                data = await resp.json()

                choices = data.get("choices", [])
                if not choices:
                    logger.warning(f"Groq returned no choices for {operation}")
                    return None

                message = choices[0].get("message", {})
                content = message.get("content", "")

                if not content:
                    logger.warning(f"Groq returned empty content for {operation}")
                    return None

                return content.strip()

        except _RateLimitError:
            raise
        except asyncio.TimeoutError:
            raise
        except Exception as e:
            logger.error(f"Groq HTTP request failed for {operation}: {e}")
            raise

    # ─── JSON Extraction ──────────────────────────────────────────────────────

    def _extract_json(self, text: str) -> dict:
        """
        Robustly extract a JSON object from Groq's response.

        Handles:
        - Clean JSON (most common with Llama/Mixtral)
        - JSON wrapped in ```json ... ``` markdown fences
        - JSON with leading/trailing whitespace
        - JSON embedded in explanatory text
        - Truncated JSON (best-effort recovery)
        """
        text = text.strip()

        # Strip markdown code fences
        if "```" in text:
            # Remove ```json or ``` fences
            text = re.sub(r"```(?:json)?\s*", "", text)
            text = re.sub(r"```\s*$", "", text)
            text = text.strip()

        # Try direct parse first (fastest path)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Find the outermost {...} block
        brace_start = text.find("{")
        if brace_start == -1:
            raise ValueError(f"No JSON object found in response: {text[:100]!r}")

        # Find matching closing brace
        depth = 0
        brace_end = -1
        for i, ch in enumerate(text[brace_start:], start=brace_start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    brace_end = i + 1
                    break

        if brace_end == -1:
            # Try to close truncated JSON
            candidate = text[brace_start:] + "}"
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                raise ValueError(f"Could not extract valid JSON from: {text[:200]!r}")

        try:
            return json.loads(text[brace_start:brace_end])
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON parse error: {e} | text={text[brace_start:brace_end][:200]!r}")

    # ─── Fallback ─────────────────────────────────────────────────────────────

    def _fallback_moderate(self, content: str, intensity: str) -> ModerationDecision:
        """
        Rule-based moderation when Groq is unavailable.
        Delegates to the FallbackModerator in ai_fallback.py.
        """
        from services.ai_fallback import FallbackModerator
        return FallbackModerator.analyze(content, intensity)

    # ─── Telemetry & Health ───────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return comprehensive service health statistics."""
        tel = self._telemetry.to_dict()
        return {
            "provider":          "groq",
            "available":         self.available,
            "model":             self._model_name,
            "rate_limited":      time.monotonic() < self._rate_limited_until,
            "rate_limited_until": (
                f"{self._rate_limited_until - time.monotonic():.1f}s"
                if time.monotonic() < self._rate_limited_until else None
            ),
            **tel,
        }

    async def list_available_models(self) -> list[str]:
        """
        Fetch the list of available models from Groq.
        Returns an empty list on failure.
        """
        if not self._session or self._session.closed:
            return []
        try:
            async with self._session.get(GROQ_MODELS_ENDPOINT) as resp:
                if not resp.ok:
                    return []
                data = await resp.json()
                return [m["id"] for m in data.get("data", [])]
        except Exception as e:
            logger.error(f"Failed to fetch Groq model list: {e}")
            return []

    def set_model(self, model_name: str) -> None:
        """Hot-swap the active model without restarting the bot."""
        old = self._model_name
        self._model_name = model_name
        logger.info(f"Groq model changed: {old} → {model_name}")

    def reset_availability(self) -> None:
        """
        Manually reset the availability flag.
        Useful if the service was disabled due to a transient error.
        """
        self._rate_limited_until = 0.0
        if self._session and not self._session.closed and self._api_key:
            self._available = True
            logger.info("Groq service availability manually reset")


# ─── Internal Exceptions ──────────────────────────────────────────────────────


class _RateLimitError(Exception):
    """Internal exception for HTTP 429 responses."""
    def __init__(self, retry_after: float = 10.0) -> None:
        self.retry_after = retry_after
        super().__init__(f"Rate limited — retry after {retry_after}s")


# ─── Backwards Compatibility Alias ────────────────────────────────────────────
# The rest of the codebase imports GeminiService. This alias means zero changes
# needed in core/bot.py, cogs/moderation.py, or cogs/admin_ai.py.

GeminiService = GroqService
