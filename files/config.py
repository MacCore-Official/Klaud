"""
config.py — Environment variables and API key loading for Klaud Bot.
All sensitive values are read from a .env file. Never hardcode secrets.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Discord ────────────────────────────────────────────────────────────────────
DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
BOT_PREFIX: str = os.getenv("BOT_PREFIX", "!")

# ── Groq AI ───────────────────────────────────────────────────────────────────
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama3-70b-8192")
GROQ_MAX_TOKENS: int = int(os.getenv("GROQ_MAX_TOKENS", "1024"))
GROQ_TEMPERATURE: float = float(os.getenv("GROQ_TEMPERATURE", "0.3"))

# ── Supabase ──────────────────────────────────────────────────────────────────
SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")   # service_role key (server-side only)

# ── License ───────────────────────────────────────────────────────────────────
LICENSE_CHECK_INTERVAL: int = int(os.getenv("LICENSE_CHECK_INTERVAL", "3600"))  # seconds

# ── Moderation ────────────────────────────────────────────────────────────────
DEFAULT_WARN_LIMIT: int = int(os.getenv("DEFAULT_WARN_LIMIT", "3"))     # warns before timeout
DEFAULT_TIMEOUT_MINUTES: int = int(os.getenv("DEFAULT_TIMEOUT_MINUTES", "10"))

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


def validate_config() -> list[str]:
    """Return a list of missing required environment variable names."""
    required = {
        "DISCORD_TOKEN": DISCORD_TOKEN,
        "GROQ_API_KEY": GROQ_API_KEY,
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_KEY": SUPABASE_KEY,
    }
    return [k for k, v in required.items() if not v]
