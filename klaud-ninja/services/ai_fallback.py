"""
KLAUD-NINJA — AI Fallback Moderator
Rule-based moderation engine used when Gemini is unavailable.
Catches obvious violations using keyword lists, regex patterns,
and structural heuristics. Not a replacement for AI — but a safety net.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from services.gemini_service import ModerationAction, ModerationDecision


# ─── Pattern Libraries ───────────────────────────────────────────────────────

_SLURS: list[str] = [
    # This list is intentionally abbreviated to avoid training on harmful content.
    # In production, populate with your own curated list from a moderation library.
    "n1gger", "f4ggot", "ch1nk", "sp1c",
]

_THREATS: list[str] = [
    r"\bi(?:'m| will| am going to| gonna) (?:kill|murder|hurt|stab|shoot|rape) (?:you|u|ur)\b",
    r"\byou(?:'re| are) (?:dead|gonna die)\b",
    r"\bi know where you live\b",
    r"\bwatch your back\b",
]

_SCAM_PATTERNS: list[str] = [
    r"\bfree\s+nitro\b",
    r"\bsteam\s+gift\s*card\b",
    r"\bclick\s+(?:this\s+)?link\b.{0,40}\bfree\b",
    r"\bdiscord\s*\.?\s*gift\b",
    r"\bcrypto\s+giveaway\b",
    r"\bdouble\s+your\s+(?:btc|eth|money|crypto)\b",
    r"\bearn\s+\$[\d,]+\s+(?:daily|per day|a day)\b",
    r"\bwork\s+from\s+home\b.{0,30}\beach\b",
]

_NSFW_KEYWORDS: list[str] = [
    "pornhub", "xvideos", "onlyfans.com", "chaturbate",
]

_INVITE_PATTERN = re.compile(
    r"discord(?:app)?\.(?:com|gg)/(?:invite/)?[a-zA-Z0-9\-]+",
    re.IGNORECASE,
)

_CAPS_THRESHOLD = 0.65   # 65%+ uppercase letters = caps abuse
_CAPS_MIN_LENGTH = 10    # Only flag messages longer than this

_SPAM_REPEAT_THRESHOLD = 5  # Same char repeated N+ times

# Compiled regexes
_COMPILED_THREATS = [re.compile(p, re.IGNORECASE) for p in _THREATS]
_COMPILED_SCAMS = [re.compile(p, re.IGNORECASE) for p in _SCAM_PATTERNS]


# ─── Analyzer ────────────────────────────────────────────────────────────────

class FallbackModerator:
    """
    Static rule-based moderation.
    All methods are synchronous (called from async context via run_in_executor
    or directly since they're fast CPU-bound operations).
    """

    @classmethod
    def analyze(cls, content: str, intensity: str = "MEDIUM") -> ModerationDecision:
        """
        Analyze a message using rule-based heuristics.
        Returns a ModerationDecision matching what Gemini would return.
        """
        categories: list[str] = []
        max_severity = 0  # 0=none, 1=warn, 2=delete, 3=timeout, 4=kick, 5=ban

        lower = content.lower()

        # ── Slurs → BAN ───────────────────────────────────────────────────────
        if cls._contains_slur(lower):
            categories.append("hate_speech")
            max_severity = max(max_severity, 5)

        # ── Threats → KICK ────────────────────────────────────────────────────
        if cls._contains_threat(lower):
            categories.append("threat")
            max_severity = max(max_severity, 4)

        # ── Scams → TIMEOUT ───────────────────────────────────────────────────
        if cls._contains_scam(lower):
            categories.append("scam")
            max_severity = max(max_severity, 3)

        # ── NSFW text ─────────────────────────────────────────────────────────
        if cls._contains_nsfw(lower):
            categories.append("nsfw_text")
            max_severity = max(max_severity, 3)

        # ── Invite links ──────────────────────────────────────────────────────
        if _INVITE_PATTERN.search(content):
            categories.append("invite_link")
            max_severity = max(max_severity, 2)

        # ── Caps abuse ────────────────────────────────────────────────────────
        if cls._is_caps_abuse(content):
            categories.append("caps_abuse")
            max_severity = max(max_severity, 1)

        # ── Spam (repeated chars) ─────────────────────────────────────────────
        if cls._is_spam(content):
            categories.append("spam")
            max_severity = max(max_severity, 2)

        # ── Intensity gates ───────────────────────────────────────────────────
        intensity_min = {
            "LOW": 4,       # LOW: only kick/ban severity passes
            "MEDIUM": 2,    # MEDIUM: delete and above
            "HIGH": 1,      # HIGH: warn and above
            "EXTREME": 1,   # EXTREME: same as HIGH for rule-based
        }.get(intensity.upper(), 2)

        if max_severity < intensity_min:
            return ModerationDecision(
                action=ModerationAction.NONE,
                confidence=0.0,
                categories=[],
                reason="No violations detected by fallback engine",
                ai_generated=False,
            )

        # ── Map severity to action ────────────────────────────────────────────
        action_map = {
            1: ModerationAction.WARN,
            2: ModerationAction.DELETE,
            3: ModerationAction.TIMEOUT,
            4: ModerationAction.KICK,
            5: ModerationAction.BAN,
        }
        action = action_map.get(max_severity, ModerationAction.NONE)
        confidence = min(0.5 + (max_severity * 0.08), 0.95)  # Fallback = lower confidence

        category_str = ", ".join(categories) if categories else "policy violation"
        reason = f"[Fallback] Detected: {category_str}"

        return ModerationDecision(
            action=action,
            confidence=confidence,
            categories=categories,
            reason=reason,
            timeout_duration=600,
            delete_message=max_severity >= 2,
            ai_generated=False,
        )

    # ─── Pattern matchers ─────────────────────────────────────────────────────

    @staticmethod
    def _contains_slur(lower: str) -> bool:
        return any(slur in lower for slur in _SLURS)

    @staticmethod
    def _contains_threat(lower: str) -> bool:
        return any(p.search(lower) for p in _COMPILED_THREATS)

    @staticmethod
    def _contains_scam(lower: str) -> bool:
        return any(p.search(lower) for p in _COMPILED_SCAMS)

    @staticmethod
    def _contains_nsfw(lower: str) -> bool:
        return any(kw in lower for kw in _NSFW_KEYWORDS)

    @staticmethod
    def _is_caps_abuse(content: str) -> bool:
        if len(content) < _CAPS_MIN_LENGTH:
            return False
        letters = [c for c in content if c.isalpha()]
        if not letters:
            return False
        return (sum(1 for c in letters if c.isupper()) / len(letters)) >= _CAPS_THRESHOLD

    @staticmethod
    def _is_spam(content: str) -> bool:
        # Detect repeated character sequences like "aaaaaa" or "lolololol"
        return bool(re.search(r"(.)\1{" + str(_SPAM_REPEAT_THRESHOLD) + r",}", content))
