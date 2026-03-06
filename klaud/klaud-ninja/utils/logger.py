"""
KLAUD-NINJA — Logger
Provides a colored console logger and optional rotating file logger.
Import get_logger(name) anywhere in the project.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from typing import Optional


# ── ANSI colors ──────────────────────────────────────────────────────────────
class _C:
    RESET   = "\033[0m";   BOLD    = "\033[1m";   DIM     = "\033[2m"
    RED     = "\033[91m";  GREEN   = "\033[92m";  YELLOW  = "\033[93m"
    BLUE    = "\033[94m";  MAGENTA = "\033[95m";  CYAN    = "\033[96m"
    WHITE   = "\033[97m";  BG_RED  = "\033[41m"


class _ColorFormatter(logging.Formatter):
    _STYLES: dict[int, tuple[str, str]] = {
        logging.DEBUG:    (f"{_C.BLUE}DEBUG   {_C.RESET}", _C.DIM),
        logging.INFO:     (f"{_C.GREEN}INFO    {_C.RESET}", ""),
        logging.WARNING:  (f"{_C.YELLOW}WARNING {_C.RESET}", _C.YELLOW),
        logging.ERROR:    (f"{_C.RED}ERROR   {_C.RESET}", _C.RED),
        logging.CRITICAL: (f"{_C.BG_RED}{_C.WHITE}CRITICAL{_C.RESET}", f"{_C.RED}{_C.BOLD}"),
    }

    def __init__(self, color: bool = True) -> None:
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        ts    = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        level_label, msg_color = self._STYLES.get(record.levelno, (record.levelname.ljust(8), ""))

        if not self.color:
            level_label = record.levelname.ljust(8)
            msg_color   = ""

        name   = record.name[:32].ljust(32)
        n_str  = f"{_C.CYAN}{name}{_C.RESET}" if self.color else name
        ts_str = f"{_C.DIM}{ts}{_C.RESET}"    if self.color else ts
        msg    = record.getMessage()
        if self.color and msg_color:
            msg = f"{msg_color}{msg}{_C.RESET}"

        exc = ""
        if record.exc_info:
            exc = "\n" + self.formatException(record.exc_info)
            if self.color:
                exc = f"{_C.RED}{exc}{_C.RESET}"

        return f"[{ts_str}] [{level_label}] [{n_str}] {msg}{exc}"


def setup_logging(level: str = "INFO", log_file: Optional[str] = None) -> None:
    """Call once at startup."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    use_color = sys.stdout.isatty() or os.getenv("FORCE_COLOR", "").lower() in ("1", "true")
    console   = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(_ColorFormatter(color=use_color))
    root.addHandler(console)

    if log_file:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(_ColorFormatter(color=False))
        root.addHandler(fh)

    # Silence noisy third-party loggers
    for name in ["discord.gateway", "discord.http", "discord.client",
                 "discord.state", "groq", "httpcore", "httpx",
                 "asyncio", "aiohttp.access"]:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the 'klaud' namespace."""
    if not name.startswith("klaud."):
        name = f"klaud.{name}"
    return logging.getLogger(name)
