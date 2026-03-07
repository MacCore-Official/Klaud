"""
KLAUD-NINJA — Configuration Settings
Centralised environment loading and validation.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("klaud.config")


def _int_env(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        logger.warning(f"Invalid value for {key}, using default {default}")
        return default


def _optional_int(key: str) -> Optional[int]:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


@dataclass
class Settings:
    # ─── Discord ──────────────────────────────────────────────────────────────
    DISCORD_TOKEN: str = field(
        default_factory=lambda: os.getenv("DISCORD_TOKEN", "")
    )
    DISCORD_APPLICATION_ID: str = field(
        default_factory=lambda: os.getenv("DISCORD_APPLICATION_ID", "")
    )

    # ─── Owner ────────────────────────────────────────────────────────────────
    BOT_OWNER_ID: int = field(
        default_factory=lambda: _int_env("BOT_OWNER_ID", 1269145029943758899)
    )
    OWNER_TEST_SERVER_ID: Optional[int] = field(
        default_factory=lambda: _optional_int("OWNER_TEST_SERVER_ID")
    )

    # ─── Database ─────────────────────────────────────────────────────────────
    DATABASE_URL: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "")
    )
    SQLITE_FALLBACK_PATH: str = field(
        default_factory=lambda: os.getenv("SQLITE_FALLBACK_PATH", "./data/klaud_fallback.db")
    )
    DB_POOL_MIN_SIZE: int = field(
        default_factory=lambda: _int_env("DB_POOL_MIN_SIZE", 2)
    )
    DB_POOL_MAX_SIZE: int = field(
        default_factory=lambda: _int_env("DB_POOL_MAX_SIZE", 10)
    )

    # ─── AI / Groq ────────────────────────────────────────────────────────────
    AI_API_KEY: str = field(
        default_factory=lambda: os.getenv("AI_API_KEY", "") or os.getenv("GROQ_API_KEY", "")
    )
    GROQ_MODEL: str = field(
        default_factory=lambda: os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    )
    AI_TIMEOUT: int = field(
        default_factory=lambda: _int_env("AI_TIMEOUT", 15)
    )
    AI_MAX_RETRIES: int = field(
        default_factory=lambda: _int_env("AI_MAX_RETRIES", 3)
    )

    # ─── Licensing ────────────────────────────────────────────────────────────
    LICENSE_SECRET: str = field(
        default_factory=lambda: os.getenv("LICENSE_SECRET", "")
    )
    LICENSE_CACHE_TTL: int = field(
        default_factory=lambda: _int_env("LICENSE_CACHE_TTL", 300)
    )

    # ─── Moderation ───────────────────────────────────────────────────────────
    MOD_LOG_CHANNEL_NAME: str = field(
        default_factory=lambda: os.getenv("MOD_LOG_CHANNEL_NAME", "klaud-mod-log")
    )
    DEFAULT_TIMEOUT_DURATION: int = field(
        default_factory=lambda: _int_env("DEFAULT_TIMEOUT_DURATION", 600)
    )
    SPAM_THRESHOLD_MESSAGES: int = field(
        default_factory=lambda: _int_env("SPAM_THRESHOLD_MESSAGES", 5)
    )
    SPAM_THRESHOLD_SECONDS: int = field(
        default_factory=lambda: _int_env("SPAM_THRESHOLD_SECONDS", 5)
    )

    # ─── Logging ──────────────────────────────────────────────────────────────
    LOG_LEVEL: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper()
    )
    LOG_FILE: Optional[str] = field(
        default_factory=lambda: os.getenv("LOG_FILE") or None
    )

    def validate(self) -> None:
        errors: list[str] = []

        if not self.DISCORD_TOKEN:
            errors.append("DISCORD_TOKEN is required")

        if not self.DATABASE_URL:
            logger.warning(
                "DATABASE_URL not set — bot will use SQLite fallback. "
                "All guilds will be treated as UNLICENSED on fallback."
            )

        if not self.AI_API_KEY:
            logger.warning(
                "AI_API_KEY not set — AI features will use fallback rule-based engine only."
            )

        if not self.LICENSE_SECRET:
            logger.warning(
                "LICENSE_SECRET not set — set this in production!"
            )

        if self.LOG_LEVEL not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            logger.warning(f"Unknown LOG_LEVEL '{self.LOG_LEVEL}', defaulting to INFO")
            self.LOG_LEVEL = "INFO"

        if errors:
            raise ValueError(
                "KLAUD startup failed — missing required config:\n"
                + "\n".join(f"  • {e}" for e in errors)
            )

        logger.info(
            f"Settings validated | owner={self.BOT_OWNER_ID} | "
            f"model={self.GROQ_MODEL} | log_level={self.LOG_LEVEL}"
        )

    def is_owner(self, user_id: int) -> bool:
        return user_id == self.BOT_OWNER_ID

    def is_owner_server(self, guild_id: int) -> bool:
        return (
            self.OWNER_TEST_SERVER_ID is not None
            and guild_id == self.OWNER_TEST_SERVER_ID
        )
