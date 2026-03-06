"""
KLAUD-NINJA — AI Fallback Moderation Engine
================================================================================
Rule-based moderation used when Groq AI is unavailable, rate-limited, or
returns an unusable response. Designed to be a reliable safety net that catches
the most obvious and dangerous violations even in degraded mode.

This module is intentionally self-contained — it imports nothing from the AI
service layer, only from itself and the standard library. This ensures it can
always run even if AI dependencies are missing.

Design philosophy:
  - Fast: All operations are synchronous and regex-based. No I/O.
  - Safe: Errs on the side of caution for severe categories (threats, slurs).
  - Conservative: Intentionally under-flags borderline content to avoid false positives.
  - Transparent: Returns ai_generated=False so operators know this is fallback output.

Pattern coverage:
  - Slurs and hate speech (abbreviated — populate with your own list in production)
  - Direct threats and doxxing
  - Scam patterns (free Nitro, crypto giveaways, phishing)
  - NSFW platform links
  - Unauthorized Discord invite links
  - Caps abuse (shouting)
  - Repetitive spam characters
  - Mass mention spam
  - Suspicious URL patterns

Limitations:
  - No semantic understanding — misses subtle harassment and context-dependent content
  - Pattern lists must be manually maintained
  - No confidence calibration against real data
  - Cannot detect image-based violations

Usage:
    from services.ai_fallback import FallbackModerator
    decision = FallbackModerator.analyze(message_content, intensity="HIGH")
================================================================================
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ModerationAction(str, Enum):
    NONE    = "none"
    WARN    = "warn"
    DELETE  = "delete"
    TIMEOUT = "timeout"
    KICK    = "kick"
    BAN     = "ban"

    @property
    def severity(self) -> int:
        return {"none": 0, "warn": 1, "delete": 2, "timeout": 3, "kick": 4, "ban": 5}[self.value]

    @property
    def implies_delete(self) -> bool:
        return self.severity >= 2


@dataclass
class ModerationDecision:
    action: ModerationAction        = ModerationAction.NONE
    confidence: float               = 0.0
    categories: list[str]           = field(default_factory=list)
    reason: str                     = ""
    timeout_duration: int           = 600
    delete_message: bool            = False
    ai_generated: bool              = False
    request_id: str                 = ""
    model_used: str                 = "fallback"
    latency_ms: float               = 0.0
    tokens_used: int                = 0

    @classmethod
    def safe_default(cls) -> "ModerationDecision":
        return cls(action=ModerationAction.NONE, reason="AI unavailable — no action taken")

    @classmethod
    def no_violation(cls) -> "ModerationDecision":
        return cls(
            action=ModerationAction.NONE,
            confidence=0.0,
            categories=[],
            reason="No violations detected by fallback engine",
            ai_generated=False,
        )


_HATE_SPEECH_PATTERNS: list[str] = [
    r"\bn[i1!][g9][g9][ae3][r4]\b",
    r"\bf[a4][g9][g9][o0][t7]\b",
    r"\bc[h]1[n][k]\b",
    r"\bs[p][1i][c]\b",
    r"\bk[y][k][e3]\b",
]

_THREAT_PATTERNS: list[str] = [
    r"\bi(?:'m|\s+am|\s+will|\s+gonna|\s+am\s+going\s+to)\s+(?:kill|murder|hurt|stab|shoot|rape|beat|attack)\s+(?:you|u|ur|you\s+all)\b",
    r"\byou(?:'re|\s+are)\s+(?:dead|gonna\s+die|going\s+to\s+die)\b",
    r"\bi\s+know\s+where\s+you\s+live\b",
    r"\bwatch\s+your\s+(?:back|ass)\b",
    r"\byou(?:'ll|\s+will)\s+(?:regret|pay\s+for)\s+(?:this|that)\b",
    r"\bdox(?:xx?ing|ed?)\s+(?:you|u)\b",
]

_SCAM_PATTERNS: list[str] = [
    r"\bfree\s+(?:discord\s+)?nitro\b",
    r"\bdiscord\s*(?:\.com|\.gg|app)?[\s/\\]*(?:nitro|gift|giveaway)\b",
    r"\bsteam\s+gift\s*(?:card|code)\b",
    r"\bclaim\s+(?:your\s+)?(?:free\s+)?(?:nitro|prize|reward|gift)\b",
    r"\bcrypto\s+giveaway\b",
    r"\bdouble\s+(?:your\s+)?(?:btc|eth|crypto|bitcoin|money)\b",
    r"\bsend\s+(?:\d+\s+)?(?:btc|eth|bitcoin|ethereum)\s+(?:and\s+)?(?:get|receive|earn)\b",
    r"\b(?:earn|make)\s+\$[\d,]+\s+(?:per\s+day|daily|a\s+day|every\s+day)\b",
    r"\bclick\s+(?:this\s+)?(?:link|here|below)\b.{0,50}\bfree\b",
    r"\byour\s+account\s+(?:has\s+been\s+)?(?:compromised|hacked|flagged|suspended)\b",
    r"\bonlyfans\.com/\S+",
]

_NSFW_DOMAIN_PATTERNS: list[str] = [
    r"\bpornhub\.com\b",
    r"\bxvideos\.com\b",
    r"\bxnxx\.com\b",
    r"\bchaturbate\.com\b",
    r"\bxhamster\.com\b",
    r"\bredtube\.com\b",
]

_DISCORD_INVITE_PATTERN = re.compile(
    r"(?:discord(?:app)?\.(?:com|gg)|dsc\.gg|disboard\.org)"
    r"[/\\](?:invite[/\\])?[a-zA-Z0-9\-]+",
    re.IGNORECASE,
)

_CAPS_RATIO_THRESHOLD   = 0.70
_CAPS_MIN_LENGTH        = 12
_SPAM_PATTERN           = re.compile(r"(.)\1{5,}")
_MASS_MENTION_THRESHOLD = 5
_MENTION_PATTERN        = re.compile(r"<@[!&]?\d+>|@(?:everyone|here)")

_RE_FLAGS = re.IGNORECASE | re.UNICODE
_COMPILED_HATE    = [re.compile(p, _RE_FLAGS) for p in _HATE_SPEECH_PATTERNS]
_COMPILED_THREATS = [re.compile(p, _RE_FLAGS) for p in _THREAT_PATTERNS]
_COMPILED_SCAMS   = [re.compile(p, _RE_FLAGS) for p in _SCAM_PATTERNS]
_COMPILED_NSFW    = [re.compile(p, _RE_FLAGS) for p in _NSFW_DOMAIN_PATTERNS]


class _Severity:
    NONE    = 0
    WARN    = 1
    DELETE  = 2
    TIMEOUT = 3
    KICK    = 4
    BAN     = 5


_INTENSITY_GATES = {
    "LOW":     _Severity.KICK,
    "MEDIUM":  _Severity.DELETE,
    "HIGH":    _Severity.WARN,
    "EXTREME": _Severity.WARN,
}


@dataclass
class _DetectionResult:
    detected:   bool
    category:   str
    severity:   int
    reason:     str
    confidence: float


class FallbackModerator:
    """
    Static rule-based moderation engine.
    All methods are synchronous and complete in microseconds.
    Safe to call directly from async context.
    """

    @classmethod
    def analyze(cls, content: str, intensity: str = "MEDIUM") -> ModerationDecision:
        t_start = time.monotonic()

        if not content or not content.strip():
            return ModerationDecision.no_violation()

        normalized = cls._normalize(content)
        lower      = content.lower()

        detections: list[_DetectionResult] = [
            cls._detect_hate_speech(normalized, lower),
            cls._detect_threats(normalized, lower),
            cls._detect_scams(normalized, lower),
            cls._detect_nsfw(normalized, lower),
            cls._detect_invite_links(content),
            cls._detect_caps_abuse(content),
            cls._detect_spam(content),
            cls._detect_mass_mentions(content),
        ]

        triggered = [d for d in detections if d.detected]
        if not triggered:
            return ModerationDecision.no_violation()

        max_detection  = max(triggered, key=lambda d: d.severity)
        all_categories = list({d.category for d in triggered})

        gate = _INTENSITY_GATES.get(intensity.upper(), _Severity.DELETE)
        if max_detection.severity < gate:
            return ModerationDecision.no_violation()

        action = cls._severity_to_action(max_detection.severity)

        base_conf = {
            _Severity.WARN:    0.65,
            _Severity.DELETE:  0.72,
            _Severity.TIMEOUT: 0.78,
            _Severity.KICK:    0.85,
            _Severity.BAN:     0.91,
        }.get(max_detection.severity, 0.60)

        if len(triggered) > 1:
            base_conf = min(base_conf + 0.05 * (len(triggered) - 1), 0.95)

        latency      = (time.monotonic() - t_start) * 1000
        category_str = ", ".join(all_categories)
        reason       = f"[Fallback] {max_detection.reason} | Detected: {category_str}"

        return ModerationDecision(
            action=action,
            confidence=base_conf,
            categories=all_categories,
            reason=reason[:500],
            timeout_duration=600,
            delete_message=action.implies_delete,
            ai_generated=False,
            model_used="fallback",
            latency_ms=latency,
        )

    @classmethod
    def _detect_hate_speech(cls, normalized: str, lower: str) -> _DetectionResult:
        for pattern in _COMPILED_HATE:
            if pattern.search(normalized) or pattern.search(lower):
                return _DetectionResult(True, "hate_speech", _Severity.BAN, "Hate speech / slur detected", 0.92)
        return _DetectionResult(False, "hate_speech", 0, "", 0.0)

    @classmethod
    def _detect_threats(cls, normalized: str, lower: str) -> _DetectionResult:
        for pattern in _COMPILED_THREATS:
            if pattern.search(normalized) or pattern.search(lower):
                return _DetectionResult(True, "threat", _Severity.KICK, "Direct threat detected", 0.86)
        return _DetectionResult(False, "threat", 0, "", 0.0)

    @classmethod
    def _detect_scams(cls, normalized: str, lower: str) -> _DetectionResult:
        for pattern in _COMPILED_SCAMS:
            if pattern.search(normalized) or pattern.search(lower):
                return _DetectionResult(True, "scam", _Severity.TIMEOUT, "Scam / phishing pattern detected", 0.80)
        return _DetectionResult(False, "scam", 0, "", 0.0)

    @classmethod
    def _detect_nsfw(cls, normalized: str, lower: str) -> _DetectionResult:
        for pattern in _COMPILED_NSFW:
            if pattern.search(normalized) or pattern.search(lower):
                return _DetectionResult(True, "nsfw_text", _Severity.TIMEOUT, "NSFW platform link detected", 0.90)
        return _DetectionResult(False, "nsfw_text", 0, "", 0.0)

    @classmethod
    def _detect_invite_links(cls, content: str) -> _DetectionResult:
        if _DISCORD_INVITE_PATTERN.search(content):
            return _DetectionResult(True, "invite_link", _Severity.DELETE, "Unauthorized Discord invite link", 0.95)
        return _DetectionResult(False, "invite_link", 0, "", 0.0)

    @classmethod
    def _detect_caps_abuse(cls, content: str) -> _DetectionResult:
        if len(content) < _CAPS_MIN_LENGTH:
            return _DetectionResult(False, "caps_abuse", 0, "", 0.0)
        letters = [c for c in content if c.isalpha()]
        if len(letters) < _CAPS_MIN_LENGTH:
            return _DetectionResult(False, "caps_abuse", 0, "", 0.0)
        ratio = sum(1 for c in letters if c.isupper()) / len(letters)
        if ratio >= _CAPS_RATIO_THRESHOLD:
            return _DetectionResult(True, "caps_abuse", _Severity.WARN, f"Excessive caps ({ratio:.0%})", 0.70)
        return _DetectionResult(False, "caps_abuse", 0, "", 0.0)

    @classmethod
    def _detect_spam(cls, content: str) -> _DetectionResult:
        if _SPAM_PATTERN.search(content):
            return _DetectionResult(True, "spam", _Severity.DELETE, "Repetitive character spam", 0.75)
        return _DetectionResult(False, "spam", 0, "", 0.0)

    @classmethod
    def _detect_mass_mentions(cls, content: str) -> _DetectionResult:
        count = len(_MENTION_PATTERN.findall(content))
        if count >= _MASS_MENTION_THRESHOLD:
            sev = _Severity.TIMEOUT if count >= 8 else _Severity.WARN
            return _DetectionResult(True, "spam", sev, f"Mass mention spam ({count} mentions)", 0.82)
        return _DetectionResult(False, "spam", 0, "", 0.0)

    @staticmethod
    def _normalize(text: str) -> str:
        text = re.sub(r"[\u200b\u200c\u200d\u200e\u200f\ufeff]", "", text)
        try:
            text = unicodedata.normalize("NFC", text)
        except Exception:
            pass
        sub = str.maketrans({
            "0": "o", "1": "i", "3": "e", "4": "a",
            "5": "s", "7": "t", "8": "b", "@": "a",
            "$": "s", "!": "i", "+": "t",
        })
        return text.lower().translate(sub)

    @staticmethod
    def _severity_to_action(severity: int) -> ModerationAction:
        return {
            _Severity.WARN:    ModerationAction.WARN,
            _Severity.DELETE:  ModerationAction.DELETE,
            _Severity.TIMEOUT: ModerationAction.TIMEOUT,
            _Severity.KICK:    ModerationAction.KICK,
            _Severity.BAN:     ModerationAction.BAN,
        }.get(severity, ModerationAction.NONE)

    @classmethod
    def contains_invite_link(cls, content: str) -> bool:
        return bool(_DISCORD_INVITE_PATTERN.search(content))

    @classmethod
    def contains_scam(cls, content: str) -> bool:
        return any(p.search(content.lower()) for p in _COMPILED_SCAMS)

    @classmethod
    def is_caps_abuse(cls, content: str) -> bool:
        return cls._detect_caps_abuse(content).detected

    @classmethod
    def contains_mass_mentions(cls, content: str) -> bool:
        return cls._detect_mass_mentions(content).detected
