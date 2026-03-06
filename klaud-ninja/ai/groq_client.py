"""
KLAUD-NINJA — Groq AI Client
Async wrapper around the synchronous groq SDK.
Provides text completion and JSON-parsed completion helpers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Optional

log = logging.getLogger("klaud.groq")

try:
    from groq import Groq
    _HAS_GROQ = True
except ImportError:
    _HAS_GROQ = False
    log.warning("groq package not installed — AI features disabled")


class GroqClient:
    """
    Thin async wrapper around the Groq SDK.

    All SDK calls are synchronous; we execute them in a thread pool via
    asyncio.get_event_loop().run_in_executor so they never block the event loop.
    """

    def __init__(
        self,
        api_key:     str,
        model:       str   = "llama-3.3-70b-versatile",
        timeout:     float = 20.0,
        max_retries: int   = 3,
    ) -> None:
        self._api_key     = api_key
        self._model       = model
        self._timeout     = timeout
        self._max_retries = max_retries
        self._client: Optional[Any] = None
        self.available    = False

        # Stats
        self._calls   = 0
        self._errors  = 0
        self._last_ok: Optional[float] = None

    async def initialise(self) -> None:
        """Connect and smoke-test the Groq API. Safe to call multiple times."""
        if not _HAS_GROQ:
            return
        if not self._api_key:
            log.warning("GROQ_API_KEY is not set — AI disabled")
            return
        try:
            self._client = Groq(api_key=self._api_key)
            # Quick smoke test
            await self._call(
                system="Reply with the single word OK.",
                user="ping",
                max_tokens=5,
            )
            self.available = True
            self._last_ok  = time.monotonic()
            log.info(f"Groq ready ✓  model={self._model}")
        except Exception as exc:
            log.warning(f"Groq init failed: {exc}")

    # ── Core caller ────────────────────────────────────────────────────────────

    async def _call(
        self,
        system:      str,
        user:        str,
        max_tokens:  int   = 1024,
        temperature: float = 0.1,
    ) -> str:
        """Raw Groq chat call — returns text content or raises."""
        if not self._client:
            raise RuntimeError("Groq client not initialised")

        loop = asyncio.get_event_loop()
        resp = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user},
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature,
                ),
            ),
            timeout=self._timeout,
        )
        return resp.choices[0].message.content.strip()

    async def complete(
        self,
        system:     str,
        user:       str,
        max_tokens: int   = 1024,
        operation:  str   = "complete",
    ) -> Optional[str]:
        """
        Call Groq with exponential-backoff retries.
        Returns the text response or None on total failure.
        """
        if not self.available:
            return None

        for attempt in range(1, self._max_retries + 1):
            self._calls += 1
            try:
                text = await self._call(system=system, user=user, max_tokens=max_tokens)
                self._last_ok = time.monotonic()
                return text
            except asyncio.TimeoutError:
                self._errors += 1
                log.warning(f"[{operation}] Timeout (attempt {attempt}/{self._max_retries})")
            except Exception as exc:
                self._errors += 1
                err = str(exc).lower()
                if "invalid_api_key" in err or "401" in err:
                    log.critical("Groq: auth failed — disabling AI")
                    self.available = False
                    return None
                log.error(f"[{operation}] attempt {attempt} error: {exc}")

            if attempt < self._max_retries:
                await asyncio.sleep(min(1.5 ** attempt, 8))

        log.error(f"[{operation}] all {self._max_retries} retries exhausted")
        return None

    async def complete_json(
        self,
        system:     str,
        user:       str,
        max_tokens: int = 1024,
        operation:  str = "json",
    ) -> Optional[dict | list]:
        """
        Call Groq and parse the response as JSON.
        Strips markdown fences and single-quote fixes automatically.
        Returns parsed dict/list or None on failure.
        """
        raw = await self.complete(system=system, user=user, max_tokens=max_tokens, operation=operation)
        if raw is None:
            return None
        return self._parse_json(raw)

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_json(text: str) -> Optional[dict | list]:
        text = text.strip()
        # Strip markdown fences
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$",          "", text)
        text = text.strip()

        # Extract first JSON object or array
        for pattern in (r"\[[\s\S]+\]", r"\{[\s\S]+\}"):
            m = re.search(pattern, text)
            if m:
                text = m.group()
                break

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        try:
            return json.loads(text.replace("'", '"'))
        except json.JSONDecodeError:
            log.debug(f"JSON parse failed: {text[:200]!r}")
            return None

    def stats(self) -> dict:
        return {
            "available":   self.available,
            "model":       self._model,
            "calls":       self._calls,
            "errors":      self._errors,
            "error_rate":  round(self._errors / max(self._calls, 1), 3),
            "last_ok_secs_ago": round(time.monotonic() - self._last_ok, 1) if self._last_ok else None,
        }
