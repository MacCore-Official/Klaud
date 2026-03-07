"""
KLAUD-NINJA — Groq AI Service
═══════════════════════════════════════════════════════════════════════════════
Replaces Gemini with Groq — fast, free, and reliable.
Groq uses OpenAI-compatible chat completions API via the official groq SDK.

Supported models (set via GROQ_MODEL env var):
  llama-3.3-70b-versatile   — best quality, recommended
  llama-3.1-8b-instant      — fastest, good for high-traffic
  mixtral-8x7b-32768        — excellent at following structured instructions
  gemma2-9b-it              — lightweight alternative

Features:
  • Async wrapper with thread executor (groq SDK is sync)
  • Exponential backoff retry logic
  • Structured JSON output parsing with validation
  • Typed return dataclasses — no raw strings leak to callers
  • Full fallback to rule-based engine if Groq is unavailable
  • Service health stats for /mod status command
═══════════════════════════════════════════════════════════════════════════════
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

logger = logging.getLogger("klaud.groq_service")

try:
    from groq import Groq
    HAS_GROQ = True
except ImportError:
    HAS_GROQ = False
    logger.warning("groq package not installed — AI features will use fallback engine")


# ─── Output Models ───────────────────────────────────────────────────────────

class ModerationAction(str, Enum):
    """All possible moderation actions in escalation order."""
    NONE    = "none"
    WARN    = "warn"
    DELETE  = "delete"
    TIMEOUT = "timeout"
    KICK    = "kick"
    BAN     = "ban"


@dataclass
class ModerationDecision:
    """
    Structured output from the AI moderation analysis.
    Always returned from analyze_message() — never None.
    """
    action:           ModerationAction = ModerationAction.NONE
    confidence:       float            = 0.0
    categories:       list[str]        = field(default_factory=list)
    reason:           str              = ""
    timeout_duration: int              = 600
    delete_message:   bool             = False
    ai_generated:     bool             = True

    @classmethod
    def safe_default(cls) -> "ModerationDecision":
        """Returned when AI is completely unavailable — take no action."""
        return cls(
            action=ModerationAction.NONE,
            ai_generated=False,
            reason="AI unavailable — no action taken",
        )

    @classmethod
    def from_dict(cls, data: dict) -> "ModerationDecision":
        """Parse a Groq JSON response dict into a ModerationDecision."""
        raw_action = str(data.get("action", "none")).lower().strip()
        try:
            action = ModerationAction(raw_action)
        except ValueError:
            action = ModerationAction.NONE

        confidence = float(data.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))   # Clamp to [0, 1]

        categories = data.get("categories", [])
        if not isinstance(categories, list):
            categories = []

        return cls(
            action=action,
            confidence=confidence,
            categories=[str(c).lower() for c in categories],
            reason=str(data.get("reason", ""))[:500],
            timeout_duration=int(data.get("timeout_duration", 600)),
            delete_message=action not in (ModerationAction.NONE, ModerationAction.WARN),
            ai_generated=True,
        )

    def __str__(self) -> str:
        return (
            f"ModerationDecision(action={self.action.value}, "
            f"confidence={self.confidence:.2f}, "
            f"categories={self.categories})"
        )


@dataclass
class AdminCommandDecision:
    """
    Structured output from the AI admin command parser.
    Always returned from parse_admin_command() — never None.
    """
    action_type:           str        = ""
    parameters:            dict       = field(default_factory=dict)
    confirmation_required: bool       = False
    explanation:           str        = ""
    valid:                 bool       = False

    @classmethod
    def invalid(cls, reason: str) -> "AdminCommandDecision":
        """Return an invalid decision with a human-readable reason."""
        return cls(valid=False, explanation=reason)

    @classmethod
    def from_dict(cls, data: dict) -> "AdminCommandDecision":
        """Parse a Groq JSON response dict into an AdminCommandDecision."""
        # Actions that always require confirmation
        risky = {
            "delete_channel", "delete_category", "delete_all_channels",
            "set_permissions", "kick_user", "ban_user", "setup_basic_server",
        }
        action = str(data.get("action_type", "")).strip()

        # For multi_action, carry the nested actions list in parameters
        params = data.get("parameters", {})
        if action == "multi_action":
            params = {"actions": data.get("actions", [])}

        return cls(
            action_type=action,
            parameters=params,
            confirmation_required=(
                bool(data.get("confirmation_required", False))
                or action in risky
            ),
            explanation=str(data.get("explanation", ""))[:500],
            valid=bool(action),
        )

    def __str__(self) -> str:
        return (
            f"AdminCommandDecision(action={self.action_type}, "
            f"valid={self.valid}, confirm={self.confirmation_required})"
        )


# ─── System Prompts ──────────────────────────────────────────────────────────

_MODERATION_SYSTEM = """You are KLAUD, a strict Discord moderation AI.
Analyze the given message and respond ONLY with a valid JSON object.
No preamble, no markdown fences, no explanation — just the JSON.

Detectable violation categories:
  toxicity, harassment, spam, scam, nsfw_text, threat, hate_speech,
  profanity, caps_abuse, invite_link, raiding, self_harm_promotion

Enforcement intensity levels:
  LOW     — Only act on extreme violations: threats, hate speech, scams
  MEDIUM  — Act on clear toxicity, harassment, spam, scams. Allow mild language
  HIGH    — Act on profanity, caps abuse, invite links, any toxicity
  EXTREME — Zero tolerance. Act on anything suspicious or borderline

Action escalation order: none < warn < delete < timeout < kick < ban

Required JSON response format (no other text):
{
  "action": "none|warn|delete|timeout|kick|ban",
  "confidence": 0.0,
  "categories": [],
  "reason": "brief explanation",
  "timeout_duration": 600
}"""

_ADMIN_SYSTEM = """You are KLAUD, a smart and friendly Discord bot assistant.
You can have natural conversations AND execute server management commands.

FIRST: Decide if this is a COMMAND or a CONVERSATION.

CONVERSATION: if the message is a question, greeting, discussion, opinion, "what can you do", 
"how does X work", "tell me about", small talk, or anything that doesn't require a Discord action.
→ Respond with: {"action_type": "chat", "message": "your friendly response here"}

COMMAND: if the message wants you to DO something in the Discord server.
→ Respond with the appropriate action JSON below.

You are friendly, witty, and helpful. In chat mode you can discuss anything.

═══════════════════════════════════════════════════════
SUPPORTED COMMAND ACTION TYPES
═══════════════════════════════════════════════════════

create_category
  params: { "name": "Category Name" }

create_channel
  params: { "name": "channel-name", "category": "Category Name or null", "type": "text|voice|announcement", "topic": "optional" }

bulk_create_channels
  params: { "channels": [ {"name": "ch1", "category": "Cat", "type": "text"}, ... ] }

delete_channel
  params: { "channel_name": "exact-name" }

delete_all_channels
  params: { "confirm": true }

delete_category
  params: { "category_name": "Name", "delete_channels_inside": true }

rename_channel
  params: { "old_name": "old", "new_name": "new" }

lock_channel
  params: { "channel_name": "name or CURRENT" }

unlock_channel
  params: { "channel_name": "name or CURRENT" }

set_channel_permissions
  params: { "channel_name": "name", "role_name": "Role", "allow": ["send_messages","view_channel"], "deny": [] }
  Valid permission names: view_channel, send_messages, read_message_history, attach_files, embed_links,
  add_reactions, use_external_emojis, mention_everyone, manage_messages, manage_channels

create_role
  params: { "name": "Role Name", "color": "#hex", "hoist": false, "mentionable": false }

bulk_create_roles
  params: { "roles": [ {"name": "R1", "color": "#hex", "hoist": false}, ... ] }

delete_role
  params: { "role_name": "Name" }

edit_role_permissions
  params: { "role_name": "Role Name", "grant": ["kick_members","ban_members"], "revoke": ["administrator"] }
  Valid permission names: administrator, manage_guild, manage_channels, manage_roles, manage_messages,
  kick_members, ban_members, moderate_members, view_audit_log, mention_everyone, send_messages,
  read_messages, attach_files, embed_links, add_reactions, use_external_emojis, connect, speak,
  move_members, mute_members, deafen_members, manage_nicknames, change_nickname, manage_webhooks,
  manage_emojis, view_channel, read_message_history, send_tts_messages

move_role_to_top
  params: { "role_name": "Role Name" }
  Moves the role to just below the bot's highest role.

assign_role
  params: { "role_name": "Role Name", "user_mention": "<@userid>" }

remove_role
  params: { "role_name": "Role Name", "user_mention": "<@userid>" }

purge_messages
  params: { "amount": 10, "channel_name": "CURRENT" }

kick_user
  params: { "user_mention": "<@userid>", "reason": "reason" }

ban_user
  params: { "user_mention": "<@userid>", "reason": "reason", "delete_days": 0 }

unban_user
  params: { "user_id": "123456789", "reason": "reason" }

timeout_user
  params: { "user_mention": "<@userid>", "duration_minutes": 10, "reason": "reason" }

untimeout_user
  params: { "user_mention": "<@userid>" }

setup_verification
  params: { "channel_name": "verify", "role_name": "Verified" }

setup_basic_server
  params: {}

multi_action
  Use when instruction needs multiple steps.
  { "action_type": "multi_action", "actions": [ {...}, {...} ], "explanation": "summary" }

unknown
  params: { "reason": "why" }
  Only if genuinely impossible.

═══════════════════════════════════════════════════════
RESPONSE FORMAT
═══════════════════════════════════════════════════════

Single action:
{ "action_type": "action_name", "parameters": {...}, "explanation": "one sentence", "confirmation_required": false }

Chat:
{ "action_type": "chat", "message": "your response" }

═══════════════════════════════════════════════════════
EXAMPLES
═══════════════════════════════════════════════════════

"hey what can you do?" → {"action_type":"chat","message":"Hey! I can manage your entire server — create channels, categories, roles, kick/ban users, set permissions, lock channels, purge messages, and more. Just tell me what you need in plain English!"}

"what's 2+2?" → {"action_type":"chat","message":"4! Though I'm better at Discord math like adding channels 😄"}

"delete all channels" → {"action_type":"delete_all_channels","parameters":{"confirm":true},"explanation":"Delete every channel in the server","confirmation_required":true}

"give the Moderator role the ability to kick and ban" → {"action_type":"edit_role_permissions","parameters":{"role_name":"Moderator","grant":["kick_members","ban_members"],"revoke":[]},"explanation":"Grant kick and ban permissions to the Moderator role","confirmation_required":false}

"move the Admin role to the top" → {"action_type":"move_role_to_top","parameters":{"role_name":"Admin"},"explanation":"Move Admin role to the top of the role hierarchy","confirmation_required":false}

"create a trading category with channels buy-sell, price-check, middleman" → {"action_type":"multi_action","actions":[{"action_type":"create_category","parameters":{"name":"Trading"}},{"action_type":"bulk_create_channels","parameters":{"channels":[{"name":"buy-sell","category":"Trading","type":"text"},{"name":"price-check","category":"Trading","type":"text"},{"name":"middleman","category":"Trading","type":"text"}]}}],"explanation":"Create Trading category with 3 channels","confirmation_required":false}

"create roles for Admin, Moderator, VIP" → {"action_type":"bulk_create_roles","parameters":{"roles":[{"name":"Admin","color":"#FF0000"},{"name":"Moderator","color":"#FF8C00"},{"name":"VIP","color":"#FFD700"}]},"explanation":"Create 3 roles","confirmation_required":false}

"ban @user" → {"action_type":"ban_user","parameters":{"user_mention":"<@userid>","reason":"Banned by admin","delete_days":0},"explanation":"Ban the user","confirmation_required":false}

═══════════════════════════════════════════════════════
RULES
═══════════════════════════════════════════════════════
- confirmation_required = true ONLY for: delete_all_channels, setup_basic_server
- NEVER require confirmation for ban, kick, purge, delete_channel — admins know what they want
- Channel names: lowercase with hyphens
- If admin says "this channel" or "current channel" → channel_name = "CURRENT"
- NEVER return markdown fences or text outside JSON
- Be smart and infer intent: "nuke the chat" = purge_messages, "silence @user" = timeout_user"""


# ─── Groq Service ────────────────────────────────────────────────────────────

class GroqService:
    """
    Async Groq AI service for KLAUD-NINJA.

    All methods return typed dataclasses.
    Groq SDK is synchronous — all calls are wrapped in asyncio executor.
    Retries with exponential backoff on failure.
    Automatically falls back to rule-based engine when unavailable.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
        timeout: float = 10.0,
        max_retries: int = 3,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: Optional[Groq] = None
        self._available = False

        # Health tracking
        self._total_calls = 0
        self._total_errors = 0
        self._last_error: Optional[str] = None
        self._last_success_ts: Optional[float] = None

    async def initialise(self) -> None:
        """
        Set up the Groq client and run a connectivity test.
        Call once at bot startup. Sets self._available based on result.
        """
        if not HAS_GROQ:
            logger.warning("groq package not installed — AI features disabled")
            return

        if not self._api_key:
            logger.warning("AI_API_KEY not set — AI features disabled")
            return

        try:
            self._client = Groq(api_key=self._api_key)

            # Quick connectivity test — very short message
            test_response = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._client.chat.completions.create(
                        model=self._model,
                        messages=[{"role": "user", "content": "Reply with: OK"}],
                        max_tokens=5,
                    ),
                ),
                timeout=8.0,
            )

            if test_response and test_response.choices:
                self._available = True
                self._last_success_ts = time.monotonic()
                logger.info(
                    f"Groq AI initialised ✓ | model={self._model} | "
                    f"test_response='{test_response.choices[0].message.content.strip()}'"
                )
            else:
                logger.warning("Groq returned empty test response — running in degraded mode")

        except asyncio.TimeoutError:
            logger.warning("Groq connectivity test timed out — running in degraded mode")
        except Exception as exc:
            logger.warning(f"Groq init failed: {exc} — running in degraded mode")

    @property
    def available(self) -> bool:
        """Return True if the Groq client is initialised and reachable."""
        return self._available and self._client is not None

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
            content:      The raw message text to analyze.
            intensity:    Moderation intensity: LOW / MEDIUM / HIGH / EXTREME
            author_info:  Optional author context string for the AI.
            channel_info: Optional channel context string for the AI.

        Returns:
            ModerationDecision with action, confidence, categories, and reason.
            Falls back to rule-based analysis if Groq is unavailable.
        """
        if not self.available:
            logger.debug("Groq unavailable — using fallback moderator")
            return self._fallback_moderate(content, intensity)

        # Build the user prompt with context
        context_lines = [
            f"Intensity level: {intensity.upper()}",
            f"Message to analyze: {content!r}",
        ]
        if author_info:
            context_lines.append(f"Author context: {author_info}")
        if channel_info:
            context_lines.append(f"Channel context: {channel_info}")

        prompt = "\n".join(context_lines)

        raw = await self._chat(
            system=_MODERATION_SYSTEM,
            user=prompt,
            max_tokens=256,
            operation="moderation",
        )

        if raw is None:
            logger.warning("Groq moderation returned None — using fallback")
            return self._fallback_moderate(content, intensity)

        try:
            data = self._parse_json(raw)
            decision = ModerationDecision.from_dict(data)
            logger.debug(f"Groq moderation: {decision}")
            return decision
        except Exception as exc:
            logger.error(f"Failed to parse moderation response: {exc} | raw={raw[:300]}")
            return self._fallback_moderate(content, intensity)

    async def parse_admin_command(
        self,
        instruction: str,
        guild_context: Optional[str] = None,
    ) -> AdminCommandDecision:
        """
        Parse a natural language admin instruction into a structured action.

        Args:
            instruction:   The raw text from the admin's message.
            guild_context: Optional string describing the server's current state.

        Returns:
            AdminCommandDecision. Returns .invalid() if parsing fails.
        """
        if not self.available:
            return AdminCommandDecision.invalid(
                "AI service is currently unavailable. Please try again in a moment."
            )

        user_content = instruction
        if guild_context:
            user_content = f"Server context:\n{guild_context}\n\nAdmin instruction:\n{instruction}"

        raw = await self._chat(
            system=_ADMIN_SYSTEM,
            user=user_content,
            max_tokens=1200,
            operation="admin_command",
        )

        if raw is None:
            return AdminCommandDecision.invalid(
                "AI did not respond. Please try again."
            )

        try:
            data = self._parse_json(raw)
            decision = AdminCommandDecision.from_dict(data)
            logger.debug(f"Groq admin command: {decision}")
            return decision
        except Exception as exc:
            logger.error(f"Failed to parse admin command response: {exc} | raw={raw[:300]}")
            return AdminCommandDecision.invalid(
                "I couldn't understand that instruction. Please be more specific."
            )

    async def ask(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 512,
    ) -> Optional[str]:
        """
        Free-form Groq query. Returns raw text or None on failure.
        For internal use — prefer the typed methods above.
        """
        return await self._chat(
            system=system or "You are KLAUD, a helpful Discord bot assistant.",
            user=prompt,
            max_tokens=max_tokens,
            operation="ask",
        )

    # ─── Core caller ─────────────────────────────────────────────────────────

    async def _chat(
        self,
        system: str,
        user: str,
        max_tokens: int = 512,
        operation: str = "unknown",
    ) -> Optional[str]:
        """
        Call the Groq API with retry and exponential backoff.

        Args:
            system:     System prompt.
            user:       User message.
            max_tokens: Max tokens in the response.
            operation:  Label used in log messages.

        Returns:
            The response text, or None if all retries exhausted.
        """
        attempt = 0
        delay = 1.0

        while attempt < self._max_retries:
            attempt += 1
            self._total_calls += 1

            try:
                response = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self._client.chat.completions.create(
                            model=self._model,
                            messages=[
                                {"role": "system", "content": system},
                                {"role": "user",   "content": user},
                            ],
                            max_tokens=max_tokens,
                            temperature=0.1,    # Low temperature = consistent outputs
                        ),
                    ),
                    timeout=self._timeout,
                )

                if response and response.choices and response.choices[0].message.content:
                    text = response.choices[0].message.content.strip()
                    self._last_success_ts = time.monotonic()
                    return text

                logger.warning(
                    f"[groq] Empty response on {operation} attempt {attempt}/{self._max_retries}"
                )

            except asyncio.TimeoutError:
                self._total_errors += 1
                self._last_error = "timeout"
                logger.warning(
                    f"[groq] Timeout on {operation} "
                    f"attempt {attempt}/{self._max_retries} ({self._timeout}s)"
                )

            except Exception as exc:
                self._total_errors += 1
                self._last_error = str(exc)[:200]
                logger.error(
                    f"[groq] Error on {operation} "
                    f"attempt {attempt}/{self._max_retries}: {exc}"
                )
                # If it's an auth error, no point retrying
                err_str = str(exc).lower()
                if "invalid_api_key" in err_str or "401" in err_str or "403" in err_str:
                    logger.critical(
                        "[groq] Authentication failed — check AI_API_KEY. "
                        "Disabling AI for this session."
                    )
                    self._available = False
                    return None

            # Exponential backoff before next retry
            if attempt < self._max_retries:
                backoff = min(delay * (2 ** (attempt - 1)), 8.0)
                logger.debug(f"[groq] Retrying {operation} in {backoff:.1f}s...")
                await asyncio.sleep(backoff)

        logger.error(
            f"[groq] All {self._max_retries} retries exhausted for {operation}"
        )
        return None

    # ─── JSON parsing ────────────────────────────────────────────────────────

    def _parse_json(self, text: str) -> dict:
        """
        Extract and parse a JSON object from the AI response.
        Handles common issues: markdown fences, leading/trailing text,
        single quotes instead of double quotes.
        """
        text = text.strip()

        # Strip markdown code fences: ```json ... ``` or ``` ... ```
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

        # Find the first { ... } block in case there's preamble text
        brace_start = text.find("{")
        brace_end   = text.rfind("}") + 1
        if brace_start != -1 and brace_end > brace_start:
            text = text[brace_start:brace_end]

        # Try standard JSON parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Last resort: replace single quotes with double quotes (common LLM mistake)
        try:
            fixed = text.replace("'", '"')
            return json.loads(fixed)
        except json.JSONDecodeError:
            raise ValueError(f"Could not parse JSON from response: {text[:200]!r}")

    # ─── Fallback ────────────────────────────────────────────────────────────

    def _fallback_moderate(self, content: str, intensity: str) -> ModerationDecision:
        """Use rule-based fallback when Groq is unavailable."""
        from services.ai_fallback import FallbackModerator
        return FallbackModerator.analyze(content, intensity)

    # ─── Stats ───────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return service health statistics for /mod status."""
        uptime_since = None
        if self._last_success_ts:
            elapsed = time.monotonic() - self._last_success_ts
            uptime_since = f"{elapsed:.0f}s ago"

        return {
            "available":      self.available,
            "model":          self._model,
            "total_calls":    self._total_calls,
            "total_errors":   self._total_errors,
            "error_rate":     round(self._total_errors / max(self._total_calls, 1), 3),
            "last_error":     self._last_error,
            "last_success":   uptime_since,
        }

    def __repr__(self) -> str:
        return (
            f"GroqService(model={self._model}, "
            f"available={self._available}, "
            f"calls={self._total_calls})"
        )
