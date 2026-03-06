"""
KLAUD-NINJA — Supabase Client
Thin async wrapper around the supabase-py SDK.
One module-level client instance is used everywhere via get_client().
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from supabase import Client, create_client

log = logging.getLogger("klaud.database")

_client: Optional[Client] = None


def get_client() -> Client:
    """Return the shared Supabase client (lazy-initialised)."""
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_KEY", "")
        if not url or not key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_KEY must be set in .env"
            )
        _client = create_client(url, key)
        log.info("Supabase client initialised ✓")
    return _client
