"""
KLAUD-NINJA — Smart Logger
Provides colored console output, structured log format, module tagging,
and optional file logging for production environments.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from typing import Optional


# ANSI color codes used by colorlog
_COLORS = {
    "DEBUG": "\033[94m",       # Blue
    "INFO": "\033[92m",        # Green
    "WARNING": "\033[93m",     # Yellow
    "ERROR": "\033[91m",       # Red
    "CRITICAL": "\033[95m",    # Magenta
    "RESET": "\033[0m",
    "BOLD": "\033[1m",
    "DIM": "\033[2m",
}


class KlaudFormatter(logging.Formatter):
    """
    Custom log formatter producing colorized, structured output:
    [TIMESTAMP] [LEVEL   ] [module.name] message
    """

    LEVEL_FORMATS = {
        logging.DEBUG:    f"{_COLORS['DEBUG']}DEBUG   {_COLORS['RESET']}",
        logging.INFO:     f"{_COLORS['INFO']}INFO    {_COLORS['RESET']}",
        logging.WARNING:  f"{_COLORS['WARNING']}WARNING {_COLORS['RESET']}",
        logging.ERROR:    f"{_COLORS['ERROR']}ERROR   {_COLORS['RESET']}",
        logging.CRITICAL: f"{_COLORS['CRITICAL']}{_COLORS['BOLD']}CRITICAL{_COLORS['RESET']}",
    }

    def __init__(self, use_color: bool = True) -> None:
        super().__init__()
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        # Timestamp
        timestamp = self.formatTime(record, "%Y-%m-%d %H:%M:%S")

        # Level label
        if self.use_color:
            level_str = self.LEVEL_FORMATS.get(record.levelno, record.levelname.ljust(8))
        else:
            level_str = record.levelname.ljust(8)

        # Module tag — truncate long names on the left
        name = record.name
        if len(name) > 30:
            name = "…" + name[-29:]
        name_padded = name.ljust(30)

        if self.use_color:
            name_colored = f"{_COLORS['DIM']}{name_padded}{_COLORS['RESET']}"
            ts_colored = f"{_COLORS['DIM']}{timestamp}{_COLORS['RESET']}"
        else:
            name_colored = name_padded
            ts_colored = timestamp

        # Message
        message = record.getMessage()

        # Exception info if present
        exc_text = ""
        if record.exc_info:
            exc_text = "\n" + self.formatException(record.exc_info)

        return f"[{ts_colored}] [{level_str}] [{name_colored}] {message}{exc_text}"


class PlainFormatter(logging.Formatter):
    """Plain formatter for file output (no ANSI codes)."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        level = record.levelname.ljust(8)
        name = record.name.ljust(30)[:30]
        message = record.getMessage()
        exc_text = ""
        if record.exc_info:
            exc_text = "\n" + self.formatException(record.exc_info)
        return f"[{timestamp}] [{level}] [{name}] {message}{exc_text}"


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
) -> None:
    """
    Configure root logger and all KLAUD loggers.
    Call once at startup before anything else.

    Args:
        level: Log level string (DEBUG/INFO/WARNING/ERROR/CRITICAL)
        log_file: Optional path to write rotating log file.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Root captures everything; handlers filter

    # Remove any existing handlers to avoid duplicate output
    root_logger.handlers.clear()

    # ─── Console Handler ─────────────────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    use_color = sys.stdout.isatty() or os.getenv("FORCE_COLOR", "").lower() in ("1", "true")
    console_handler.setFormatter(KlaudFormatter(use_color=use_color))
    root_logger.addHandler(console_handler)

    # ─── File Handler (optional) ─────────────────────────────────────────────
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True) if os.path.dirname(log_file) else None
        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)  # Always capture full detail in file
        file_handler.setFormatter(PlainFormatter())
        root_logger.addHandler(file_handler)

    # ─── Silence noisy third-party loggers ───────────────────────────────────
    for noisy in (
        "discord.gateway",
        "discord.http",
        "discord.client",
        "asyncio",
        "urllib3",
        "aiohttp.access",
        "google.api_core",
        "httpcore",
        "httpx",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Retain discord.py errors
    logging.getLogger("discord").setLevel(logging.WARNING)

    logger = logging.getLogger("klaud.logger")
    logger.info(
        f"Logging initialised | level={level} | file={'yes (' + log_file + ')' if log_file else 'no'}"
    )


def get_logger(name: str) -> logging.Logger:
    """
    Convenience wrapper — returns a logger namespaced under 'klaud.'.
    Usage:  log = get_logger("moderation")  →  logger 'klaud.moderation'
    """
    if not name.startswith("klaud."):
        name = f"klaud.{name}"
    return logging.getLogger(name)
