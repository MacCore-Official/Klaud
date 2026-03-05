"""
KLAUD-NINJA — Configuration Settings
═══════════════════════════════════════════════════════════════════════════════
Single source of truth for all environment-driven configuration.
Loaded once at startup, validated, and passed to every subsystem.
All values are typed, defaulted safely, and documented.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

# Load .env file if present — silently ignored in production
load_dotenv()

logger = logging.getLogger("klaud.config")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _str(key: str, default: str = "") -> str:
    """Read a string environment variable with a default."""
    return os.getenv(key, default).strip()


def _int(key: str, default: int) -> int:
    """Read an integer environment variable with a safe fallback."""
    raw = os.getenv(key, "")
    if not raw.strip():
        return default
    try:
        return int(raw.strip())
    except (ValueError, TypeError):
        logger.warning(f"[config] Invalid integer for {key}='{raw}', using default {default}")
        return default


def _float(key: str, default: float) -> float:
    """Read a float environment variable with a safe fallback."""
    raw = os.getenv(key, "")
    if not raw.strip():
        return default
    try:
        return float(raw.strip())
    except (ValueError, TypeError):
        logger.warning(f"[config] Invalid float for {key}='{raw}', using default {default}")
        return default


def _optional_int(key: str) -> Optional[int]:
    """Read an optional integer — returns None if not set or invalid."""
    raw = os.getenv(key, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def _bool(key: str, default: bool = False) -> bool:
    """Read a boolean environment variable (true/1/yes = True)."""
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    return raw in ("true", "1", "yes", "on")


# ─── Settings Dataclass ───────────────────────────────────────────────────────

@dataclass
class Settings:
    """
    Complete KLAUD-NINJA configuration.
    Instantiated once in main.py and injected into all subsystems.
    All fields have safe defaults — only DISCORD_TOKEN is truly required.
    """

    # ── Discord ──────────────────────────────────────────────────────────────
    DISCORD_TOKEN: str = field(
        default_factory=lambda: _str("DISCORD_TOKEN")
    )
    DISCORD_APPLICATION_ID: str = field(
        default_factory=lambda: _str("DISCORD_APPLICATION_ID")
    )

    # ── Owner / Authority ────────────────────────────────────────────────────
    BOT_OWNER_ID: int = field(
        default_factory=lambda: _int("BOT_OWNER_ID", 1269145029943758899)
    )
    OWNER_TEST_SERVER_ID: Optional[int] = field(
        default_factory=lambda: _optional_int("OWNER_TEST_SERVER_ID")
    )

    # ── Database ─────────────────────────────────────────────────────────────
    DATABASE_URL: str = field(
        default_factory=lambda: _str("DATABASE_URL")
    )
    SQLITE_FALLBACK_PATH: str = field(
        default_factory=lambda: _str("SQLITE_FALLBACK_PATH", "./data/klaud_fallback.db")
    )
    DB_POOL_MIN_SIZE: int = field(
        default_factory=lambda: _int("DB_POOL_MIN_SIZE", 2)
    )
    DB_POOL_MAX_SIZE: int = field(
        default_factory=lambda: _int("DB_POOL_MAX_SIZE", 10)
    )

    # ── AI Provider (Groq) ───────────────────────────────────────────────────
    AI_API_KEY: str = field(
        default_factory=lambda: _str("AI_API_KEY")
    )
    GROQ_MODEL: str = field(
        default_factory=lambda: _str("GROQ_MODEL", "llama-3.3-70b-versatile")
    )
    AI_TIMEOUT: float = field(
        default_factory=lambda: _float("AI_TIMEOUT", 10.0)
    )
    AI_MAX_RETRIES: int = field(
        default_factory=lambda: _int("AI_MAX_RETRIES", 3)
    )

    # ── Licensing ────────────────────────────────────────────────────────────
    LICENSE_SECRET: str = field(
        default_factory=lambda: _str("LICENSE_SECRET")
    )
    LICENSE_CACHE_TTL: int = field(
        default_factory=lambda: _int("LICENSE_CACHE_TTL", 300)
    )

    # ── Moderation ───────────────────────────────────────────────────────────
    MOD_LOG_CHANNEL_NAME: str = field(
        default_factory=lambda: _str("MOD_LOG_CHANNEL_NAME", "klaud-mod-log")
    )
    DEFAULT_TIMEOUT_DURATION: int = field(
        default_factory=lambda: _int("DEFAULT_TIMEOUT_DURATION", 600)
    )
    SPAM_THRESHOLD_MESSAGES: int = field(
        default_factory=lambda: _int("SPAM_THRESHOLD_MESSAGES", 5)
    )
    SPAM_THRESHOLD_SECONDS: int = field(
        default_factory=lambda: _int("SPAM_THRESHOLD_SECONDS", 5)
    )

    # ── Logging ──────────────────────────────────────────────────────────────
    LOG_LEVEL: str = field(
        default_factory=lambda: _str("LOG_LEVEL", "INFO").upper()
    )
    LOG_FILE: Optional[str] = field(
        default_factory=lambda: _str("LOG_FILE") or None
    )

    # ─── Validation ───────────────────────────────────────────────────────────

    def validate(self) -> None:
        """
        Validate required configuration at startup.
        Logs warnings for optional missing values.
        Raises ValueError if critical config is absent.
        """
        errors: list[str] = []
        warnings: list[str] = []

        # ── Hard requirements ────────────────────────────────────────────────
        if not self.DISCORD_TOKEN:
            errors.append("DISCORD_TOKEN is required — get it from discord.com/developers")

        # ── Soft requirements (warnings only) ────────────────────────────────
        if not self.DATABASE_URL:
            warnings.append(
                "DATABASE_URL not set — using SQLite fallback. "
                "All guilds will be treated as UNLICENSED."
            )

        if not self.AI_API_KEY:
            warnings.append(
                "AI_API_KEY not set — AI moderation disabled. "
                "Bot will use rule-based fallback engine only."
            )

        if not self.LICENSE_SECRET:
            warnings.append(
                "LICENSE_SECRET not set — using random secret. "
                "Set this in production to ensure key consistency across restarts."
            )

        # ── Validate enums ────────────────────────────────────────────────────
        valid_log_levels = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
        if self.LOG_LEVEL not in valid_log_levels:
            warnings.append(f"Unknown LOG_LEVEL '{self.LOG_LEVEL}', defaulting to INFO")
            self.LOG_LEVEL = "INFO"

        valid_models = (
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "llama3-70b-8192",
            "llama3-8b-8192",
            "mixtral-8x7b-32768",
            "gemma2-9b-it",
        )
        if self.GROQ_MODEL not in valid_models:
            warnings.append(
                f"GROQ_MODEL '{self.GROQ_MODEL}' is not in the known list. "
                "It may still work if Groq has added new models."
            )

        # ── Emit warnings ────────────────────────────────────────────────────
        for w in warnings:
            logger.warning(f"[config] {w}")

        # ── Raise on errors ──────────────────────────────────────────────────
        if errors:
            raise ValueError(
                "KLAUD-NINJA cannot start due to missing configuration:\n"
                + "\n".join(f"  ✗ {e}" for e in errors)
            )

        logger.info(
            f"[config] Configuration validated ✓ | "
            f"owner={self.BOT_OWNER_ID} | "
            f"model={self.GROQ_MODEL} | "
            f"log_level={self.LOG_LEVEL}"
        )

    # ─── Utility helpers ──────────────────────────────────────────────────────

    def is_owner(self, user_id: int) -> bool:
        """Return True if the given user ID is the global bot owner."""
        return user_id == self.BOT_OWNER_ID

    def is_owner_server(self, guild_id: int) -> bool:
        """Return True if the given guild ID is the owner's test server."""
        return (
            self.OWNER_TEST_SERVER_ID is not None
            and guild_id == self.OWNER_TEST_SERVER_ID
        )

    def db_url_safe(self) -> str:
        """Return the database URL with the password redacted for logging."""
        if not self.DATABASE_URL:
            return "(not set)"
        try:
            from urllib.parse import urlparse, urlunparse
            parsed = urlparse(self.DATABASE_URL)
            safe = parsed._replace(netloc=parsed.netloc.split("@")[-1])
            return urlunparse(safe)
        except Exception:
            return "(invalid URL)"

    def __repr__(self) -> str:
        return (
            f"Settings("
            f"owner={self.BOT_OWNER_ID}, "
            f"model={self.GROQ_MODEL}, "
            f"db={self.db_url_safe()}, "
            f"log={self.LOG_LEVEL}"
            f")"
        )
