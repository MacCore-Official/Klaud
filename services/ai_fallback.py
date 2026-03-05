"""
KLAUD-NINJA — AI Fallback Moderator
═══════════════════════════════════════════════════════════════════════════════
Rule-based moderation engine used when Groq is unavailable.
Catches obvious violations using keyword matching, regex patterns,
and structural heuristics.

This is NOT a replacement for AI — it's a safety net.
All decisions are marked ai_generated=False so they can be identified.

Detection categories:
  • hate_speech    — slurs and discriminatory language
  • threat         — direct threats of violence
  • scam           — common Discord scam patterns
  • nsfw_text      — NSFW site links and keywords
  • invite_link    — unauthorized Discord invite links
  • caps_abuse     — excessive uppercase (65%+ of letters)
  • spam           — repeated characters or rapid messages
  • self_harm      — self-harm promotion keywords
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from services.groq_service import ModerationAction, ModerationDecision


# ─── Pattern Libraries ───────────────────────────────────────────────────────

# Note: Slur list is intentionally minimal here.
# In production, replace with a comprehensive curated list.
_SLURS: frozenset[str] = frozenset({
    # Abbreviated — add your own moderation wordlist here
    "n1gger", "f4ggot", "ch1nk", "sp1c", "k1ke",
})

_THREAT_PATTERNS: list[str] = [
    r"\bi(?:'m| will| am going to| gonna) (?:kill|murder|hurt|stab|shoot|rape|beat) (?:you|u|ur|y'all)\b",
    r"\byou(?:'re| are|'re) (?:dead|going to die|gonna die)\b",
    r"\bi know where you live\b",
    r"\bwatch your back\b",
    r"\bkill yourself\b",
    r"\byou should (?:die|kys|kill yourself)\b",
]

_SCAM_PATTERNS: list[str] = [
    r"\bfree\s+(?:discord\s+)?nitro\b",
    r"\bsteam\s+(?:gift\s*)?card\b",
    r"\bdiscord\s*\.?\s*gift\b",
    r"\bcrypto\s+giveaway\b",
    r"\bdouble\s+your\s+(?:btc|eth|money|crypto|bitcoin)\b",
    r"\bearn\s+\$[\d,]+\s+(?:daily|per\s+day|a\s+day|per\s+hour)\b",
    r"\bwork\s+from\s+home\b.{0,40}\beach\b",
    r"\bclick\s+(?:this\s+)?link\b.{0,40}\bfree\b",
    r"\bsend\s+(?:me\s+)?your\s+(?:wallet|seed\s+phrase|private\s+key)\b",
    r"\bverify\s+your\s+(?:account|wallet|discord)\b.{0,40}\bfree\b",
    r"\bnft\s+giveaway\b",
    r"\bairdrop\b.{0,20}\bfree\b",
]

_NSFW_KEYWORDS: frozenset[str] = frozenset({
    "pornhub", "xvideos", "xnxx", "onlyfans.com",
    "chaturbate", "cam4", "stripchat",
})

_SELF_HARM_PATTERNS: list[str] = [
    r"\bhow\s+to\s+(?:cut|harm|hurt)\s+(?:myself|yourself)\b",
    r"\bself\s*harm\s+(?:tips|methods|ways)\b",
    r"\bpro\s*ana\b",
    r"\bpro\s*mia\b",
]

_DISCORD_INVITE = re.compile(
    r"discord(?:app)?\.(?:com|gg)/(?:invite/)?[a-zA-Z0-9\-]{2,20}",
    re.IGNORECASE,
)

# Compiled pattern objects
_COMPILED_THREATS    = [re.compile(p, re.IGNORECASE) for p in _THREAT_PATTERNS]
_COMPILED_SCAMS      = [re.compile(p, re.IGNORECASE) for p in _SCAM_PATTERNS]
_COMPILED_SELF_HARM  = [re.compile(p, re.IGNORECASE) for p in _SELF_HARM_PATTERNS]

# Thresholds
_CAPS_MIN_LENGTH   = 10     # Minimum message length to check caps abuse
_CAPS_THRESHOLD    = 0.65   # 65%+ letters uppercase = caps abuse
_SPAM_REPEAT_CHARS = 6      # Same character repeated 6+ times = spam


# ─── Severity Map ────────────────────────────────────────────────────────────

# Maps category → (severity_level, confidence)
# severity: 0=none, 1=warn, 2=delete, 3=timeout, 4=kick, 5=ban
_CATEGORY_SEVERITY: dict[str, tuple[int, float]] = {
    "hate_speech":  (5, 0.92),
    "threat":       (4, 0.88),
    "self_harm":    (3, 0.85),
    "scam":         (3, 0.82),
    "nsfw_text":    (3, 0.80),
    "invite_link":  (2, 0.75),
    "spam":         (2, 0.78),
    "caps_abuse":   (1, 0.65),
}

# Intensity gates: minimum severity level to act on
_INTENSITY_MIN_SEVERITY: dict[str, int] = {
    "LOW":     4,   # Only kick/ban level
    "MEDIUM":  2,   # Delete and above
    "HIGH":    1,   # Warn and above
    "EXTREME": 1,   # Same as HIGH for rule-based
}

# Severity → ModerationAction
_SEVERITY_TO_ACTION: dict[int, ModerationAction] = {
    1: ModerationAction.WARN,
    2: ModerationAction.DELETE,
    3: ModerationAction.TIMEOUT,
    4: ModerationAction.KICK,
    5: ModerationAction.BAN,
}


# ─── Fallback Moderator ──────────────────────────────────────────────────────

class FallbackModerator:
    """
    Static rule-based message analyzer.
    Used as a safety net when Groq is unavailable.
    All methods are synchronous and CPU-bound (fast, no I/O).
    """

    @classmethod
    def analyze(cls, content: str, intensity: str = "MEDIUM") -> ModerationDecision:
        """
        Analyze a message using rule-based heuristics.
        Returns a ModerationDecision with ai_generated=False.

        Args:
            content:   The raw message text.
            intensity: Enforcement level: LOW / MEDIUM / HIGH / EXTREME.

        Returns:
            ModerationDecision — action may be NONE if nothing detected.
        """
        lower = content.lower()
        detected: list[tuple[str, int, float]] = []   # (category, severity, confidence)

        # ── Detection passes ──────────────────────────────────────────────────

        if cls._has_slur(lower):
            detected.append(("hate_speech", *_CATEGORY_SEVERITY["hate_speech"]))

        if cls._has_threat(lower):
            detected.append(("threat", *_CATEGORY_SEVERITY["threat"]))

        if cls._has_self_harm(lower):
            detected.append(("self_harm", *_CATEGORY_SEVERITY["self_harm"]))

        if cls._has_scam(lower):
            detected.append(("scam", *_CATEGORY_SEVERITY["scam"]))

        if cls._has_nsfw(lower):
            detected.append(("nsfw_text", *_CATEGORY_SEVERITY["nsfw_text"]))

        if _DISCORD_INVITE.search(content):
            detected.append(("invite_link", *_CATEGORY_SEVERITY["invite_link"]))

        if cls._is_spam(content):
            detected.append(("spam", *_CATEGORY_SEVERITY["spam"]))

        if cls._is_caps_abuse(content):
            detected.append(("caps_abuse", *_CATEGORY_SEVERITY["caps_abuse"]))

        # ── Nothing detected ──────────────────────────────────────────────────

        if not detected:
            return ModerationDecision(
                action=ModerationAction.NONE,
                confidence=0.0,
                categories=[],
                reason="No violations detected",
                ai_generated=False,
            )

        # ── Find highest severity ─────────────────────────────────────────────

        max_severity   = max(sev for _, sev, _ in detected)
        max_confidence = max(conf for _, sev, conf in detected if sev == max_severity)
        categories     = [cat for cat, _, _ in detected]

        # ── Intensity gate ────────────────────────────────────────────────────

        min_severity = _INTENSITY_MIN_SEVERITY.get(intensity.upper(), 2)
        if max_severity < min_severity:
            return ModerationDecision(
                action=ModerationAction.NONE,
                confidence=0.0,
                categories=categories,
                reason=f"Detected {categories} but below {intensity} threshold",
                ai_generated=False,
            )

        # ── Build decision ────────────────────────────────────────────────────

        action = _SEVERITY_TO_ACTION.get(max_severity, ModerationAction.NONE)
        cat_str = ", ".join(categories)
        reason = f"[Fallback engine] Detected: {cat_str}"

        return ModerationDecision(
            action=action,
            confidence=max_confidence,
            categories=categories,
            reason=reason,
            timeout_duration=600,
            delete_message=max_severity >= 2,
            ai_generated=False,
        )

    # ─── Individual detectors ─────────────────────────────────────────────────

    @staticmethod
    def _has_slur(lower: str) -> bool:
        return any(slur in lower for slur in _SLURS)

    @staticmethod
    def _has_threat(lower: str) -> bool:
        return any(p.search(lower) for p in _COMPILED_THREATS)

    @staticmethod
    def _has_self_harm(lower: str) -> bool:
        return any(p.search(lower) for p in _COMPILED_SELF_HARM)

    @staticmethod
    def _has_scam(lower: str) -> bool:
        return any(p.search(lower) for p in _COMPILED_SCAMS)

    @staticmethod
    def _has_nsfw(lower: str) -> bool:
        return any(kw in lower for kw in _NSFW_KEYWORDS)

    @staticmethod
    def _is_caps_abuse(content: str) -> bool:
        if len(content) < _CAPS_MIN_LENGTH:
            return False
        letters = [c for c in content if c.isalpha()]
        if len(letters) < _CAPS_MIN_LENGTH:
            return False
        upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
        return upper_ratio >= _CAPS_THRESHOLD

    @staticmethod
    def _is_spam(content: str) -> bool:
        # Detect repeated characters: "aaaaaaa" or "lolololol"
        pattern = r"(.)\1{" + str(_SPAM_REPEAT_CHARS) + r",}"
        return bool(re.search(pattern, content))
