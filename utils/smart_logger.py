"""
KLAUD-NINJA — Smart Logger
═══════════════════════════════════════════════════════════════════════════════
Provides structured, colorized console logging with optional file output.
Designed for both local development (rich colors) and production (plain text).

Features:
  • ANSI color-coded log levels
  • Consistent timestamp + level + module format
  • Rotating file handler (optional)
  • Silences noisy third-party loggers
  • Thread-safe and async-safe
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from typing import Optional


# ─── ANSI Color Codes ────────────────────────────────────────────────────────

class _C:
    """ANSI escape codes for terminal colors."""
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    BLACK   = "\033[30m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"
    BG_RED  = "\033[41m"


# ─── Formatters ──────────────────────────────────────────────────────────────

class ColorFormatter(logging.Formatter):
    """
    Colorized log formatter for console output.
    Format: [TIMESTAMP] [LEVEL   ] [module.name                  ] message
    """

    # Level → (label string with color, message color)
    _LEVEL_STYLES: dict[int, tuple[str, str]] = {
        logging.DEBUG: (
            f"{_C.BLUE}DEBUG   {_C.RESET}",
            _C.DIM,
        ),
        logging.INFO: (
            f"{_C.GREEN}INFO    {_C.RESET}",
            "",
        ),
        logging.WARNING: (
            f"{_C.YELLOW}WARNING {_C.RESET}",
            _C.YELLOW,
        ),
        logging.ERROR: (
            f"{_C.RED}ERROR   {_C.RESET}",
            _C.RED,
        ),
        logging.CRITICAL: (
            f"{_C.BG_RED}{_C.WHITE}{_C.BOLD}CRITICAL{_C.RESET}",
            f"{_C.RED}{_C.BOLD}",
        ),
    }

    def __init__(self, use_color: bool = True) -> None:
        super().__init__()
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        # ── Timestamp ─────────────────────────────────────────────────────────
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        if self.use_color:
            ts_str = f"{_C.DIM}{ts}{_C.RESET}"
        else:
            ts_str = ts

        # ── Level label ───────────────────────────────────────────────────────
        level_label, msg_color = self._LEVEL_STYLES.get(
            record.levelno,
            (record.levelname.ljust(8), ""),
        )
        if not self.use_color:
            level_label = record.levelname.ljust(8)
            msg_color = ""

        # ── Module name ───────────────────────────────────────────────────────
        name = record.name
        # Left-truncate if too long: "klaud.services.groq_service" → "…groq_service"
        max_name = 32
        if len(name) > max_name:
            name = "…" + name[-(max_name - 1):]
        name_padded = name.ljust(max_name)
        if self.use_color:
            name_str = f"{_C.CYAN}{name_padded}{_C.RESET}"
        else:
            name_str = name_padded

        # ── Message ───────────────────────────────────────────────────────────
        message = record.getMessage()
        if self.use_color and msg_color:
            message = f"{msg_color}{message}{_C.RESET}"

        # ── Exception ─────────────────────────────────────────────────────────
        exc_text = ""
        if record.exc_info:
            exc_text = "\n" + self.formatException(record.exc_info)
            if self.use_color:
                exc_text = f"{_C.RED}{exc_text}{_C.RESET}"

        return f"[{ts_str}] [{level_label}] [{name_str}] {message}{exc_text}"


class PlainFormatter(logging.Formatter):
    """
    Plain text formatter for file output — no ANSI escape codes.
    Same layout as ColorFormatter but safe for log files and log aggregators.
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        level = record.levelname.ljust(8)
        name = record.name.ljust(32)[:32]
        message = record.getMessage()
        exc_text = ""
        if record.exc_info:
            exc_text = "\n" + self.formatException(record.exc_info)
        return f"[{ts}] [{level}] [{name}] {message}{exc_text}"


# ─── Setup ───────────────────────────────────────────────────────────────────

def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
) -> None:
    """
    Configure the root logger for KLAUD-NINJA.
    Call exactly once at startup, before any other imports that use logging.

    Args:
        level:    Log level string. One of DEBUG/INFO/WARNING/ERROR/CRITICAL.
        log_file: Optional path for a rotating file handler. If None, only
                  stdout is used.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)   # Root captures all; handlers filter by level
    root.handlers.clear()          # Prevent duplicate handlers on reload

    # ── Console handler ───────────────────────────────────────────────────────
    # Detect whether the terminal supports ANSI codes
    use_color = (
        sys.stdout.isatty()
        or os.getenv("FORCE_COLOR", "").lower() in ("1", "true", "yes")
    ) and os.name != "nt"   # Disable on Windows unless explicitly forced

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(numeric_level)
    console.setFormatter(ColorFormatter(use_color=use_color))
    root.addHandler(console)

    # ── File handler (optional) ───────────────────────────────────────────────
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_file,
            maxBytes=10 * 1024 * 1024,   # 10 MB per file
            backupCount=7,               # Keep 7 rotated files
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)   # Always full detail in file
        file_handler.setFormatter(PlainFormatter())
        root.addHandler(file_handler)

    # ── Silence noisy third-party loggers ─────────────────────────────────────
    _quiet = [
        "discord.gateway",
        "discord.http",
        "discord.client",
        "discord.webhook",
        "discord.state",
        "asyncio",
        "urllib3",
        "urllib3.connectionpool",
        "aiohttp.access",
        "aiohttp.client",
        "httpcore",
        "httpx",
        "groq",
        "groq._base_client",
        "openai",
    ]
    for name in _quiet:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Keep discord errors visible
    logging.getLogger("discord").setLevel(logging.WARNING)

    # ── Confirm initialisation ────────────────────────────────────────────────
    log = logging.getLogger("klaud.logger")
    log.info(
        f"Logging initialised | level={level.upper()} | "
        f"color={'yes' if use_color else 'no'} | "
        f"file={'yes (' + log_file + ')' if log_file else 'no'}"
    )


# ─── Convenience ─────────────────────────────────────────────────────────────

def get_logger(name: str) -> logging.Logger:
    """
    Return a logger namespaced under 'klaud.'.
    Usage:
        log = get_logger("moderation")
        # Returns logger named 'klaud.moderation'
    """
    if not name.startswith("klaud."):
        name = f"klaud.{name}"
    return logging.getLogger(name)
