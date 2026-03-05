"""
KLAUD-NINJA — Database Connection Layer
═══════════════════════════════════════════════════════════════════════════════
Manages PostgreSQL (primary) and SQLite (automatic fallback) connections.
Provides a unified async query interface used by all services and cogs.

Key design decisions:
  • statement_cache_size=0 — required for Supabase/PgBouncer compatibility
  • All queries go through fetchrow / fetch / execute / fetchval helpers
  • SQLite fallback uses aiosqlite with a mutex lock for safety
  • Schema is created automatically on first connection
  • Connection mode is exposed via self.mode for conditional logic

PostgreSQL schema includes:
  licenses, guild_settings, mod_actions, user_punishments, audit_log
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from enum import Enum, auto
from typing import Any, AsyncGenerator, Optional

import aiosqlite

try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False

logger = logging.getLogger("klaud.database")


# ─── Schema Definitions ──────────────────────────────────────────────────────

POSTGRES_SCHEMA = """
-- ── License keys ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS licenses (
    id              SERIAL PRIMARY KEY,
    license_key     TEXT NOT NULL UNIQUE,
    server_id       BIGINT,
    owner_id        BIGINT,
    tier            TEXT NOT NULL DEFAULT 'BASIC',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    redeemed_at     TIMESTAMPTZ,
    redeemed_by     BIGINT
);

-- ── Per-guild configuration ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id                BIGINT PRIMARY KEY,
    mod_intensity           TEXT NOT NULL DEFAULT 'MEDIUM',
    mod_log_channel_id      BIGINT,
    verification_channel_id BIGINT,
    verification_role_id    BIGINT,
    welcome_channel_id      BIGINT,
    ai_admin_enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Moderation action log ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mod_actions (
    id              SERIAL PRIMARY KEY,
    guild_id        BIGINT NOT NULL,
    user_id         BIGINT NOT NULL,
    moderator_id    BIGINT NOT NULL,
    action          TEXT NOT NULL,
    reason          TEXT,
    message_content TEXT,
    channel_id      BIGINT,
    duration_secs   INT,
    ai_confidence   REAL,
    ai_categories   TEXT[],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Per-user warning counters ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_punishments (
    id          SERIAL PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    warn_count  INT NOT NULL DEFAULT 0,
    last_action TEXT,
    last_at     TIMESTAMPTZ,
    UNIQUE(guild_id, user_id)
);

-- ── Admin AI audit log ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id          SERIAL PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    action_type TEXT NOT NULL,
    details     JSONB,
    success     BOOLEAN NOT NULL DEFAULT TRUE,
    error_msg   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Indexes ──────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_licenses_server   ON licenses(server_id);
CREATE INDEX IF NOT EXISTS idx_licenses_key      ON licenses(license_key);
CREATE INDEX IF NOT EXISTS idx_mod_guild_ts      ON mod_actions(guild_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mod_guild_user    ON mod_actions(guild_id, user_id);
CREATE INDEX IF NOT EXISTS idx_audit_guild       ON audit_log(guild_id, created_at DESC);
"""

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS licenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    license_key TEXT NOT NULL UNIQUE,
    server_id   INTEGER,
    owner_id    INTEGER,
    tier        TEXT NOT NULL DEFAULT 'BASIC',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    redeemed_at TEXT,
    redeemed_by INTEGER
);

CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id                INTEGER PRIMARY KEY,
    mod_intensity           TEXT NOT NULL DEFAULT 'MEDIUM',
    mod_log_channel_id      INTEGER,
    verification_channel_id INTEGER,
    verification_role_id    INTEGER,
    welcome_channel_id      INTEGER,
    ai_admin_enabled        INTEGER NOT NULL DEFAULT 1,
    updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS mod_actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    moderator_id    INTEGER NOT NULL,
    action          TEXT NOT NULL,
    reason          TEXT,
    message_content TEXT,
    channel_id      INTEGER,
    duration_secs   INTEGER,
    ai_confidence   REAL,
    ai_categories   TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_punishments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    warn_count  INTEGER NOT NULL DEFAULT 0,
    last_action TEXT,
    last_at     TEXT,
    UNIQUE(guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    details     TEXT,
    success     INTEGER NOT NULL DEFAULT 1,
    error_msg   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# ─── Mode Enum ────────────────────────────────────────────────────────────────

class DatabaseMode(Enum):
    POSTGRES    = auto()
    SQLITE      = auto()
    UNAVAILABLE = auto()


# ─── Connection Class ────────────────────────────────────────────────────────

class DatabaseConnection:
    """
    Unified async database interface.

    Tries PostgreSQL first. If PG is unreachable, automatically falls back
    to SQLite. All query methods work identically in both modes.

    Usage:
        db = DatabaseConnection(url, fallback_path)
        await db.connect()
        row = await db.fetchrow("SELECT * FROM licenses WHERE server_id = $1", guild_id)
        await db.close()
    """

    def __init__(
        self,
        database_url: str,
        sqlite_path: str,
        pool_min: int = 2,
        pool_max: int = 10,
    ) -> None:
        self._database_url = database_url
        self._sqlite_path = sqlite_path
        self._pool_min = pool_min
        self._pool_max = pool_max

        self._pg_pool: Optional[asyncpg.Pool] = None
        self._sqlite_lock = asyncio.Lock()
        self.mode: DatabaseMode = DatabaseMode.UNAVAILABLE

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """
        Establish the database connection.
        Tries PostgreSQL first, falls back to SQLite automatically.
        Call once at bot startup.
        """
        if self._database_url and HAS_ASYNCPG:
            try:
                logger.info("Connecting to PostgreSQL...")
                self._pg_pool = await asyncpg.create_pool(
                    dsn=self._database_url,
                    min_size=self._pool_min,
                    max_size=self._pool_max,
                    command_timeout=30,
                    statement_cache_size=0,   # ← Required for Supabase/PgBouncer
                )
                await self._init_postgres_schema()
                self.mode = DatabaseMode.POSTGRES
                logger.info("PostgreSQL connected and schema initialised ✓")
                return

            except Exception as exc:
                logger.error(f"PostgreSQL connection failed: {exc}")
                logger.warning("Falling back to SQLite...")

        # SQLite fallback
        try:
            path_dir = os.path.dirname(self._sqlite_path)
            if path_dir:
                os.makedirs(path_dir, exist_ok=True)
            await self._init_sqlite_schema()
            self.mode = DatabaseMode.SQLITE
            logger.warning(
                f"Running on SQLite fallback: {self._sqlite_path} | "
                "All guilds without explicit licenses will be denied."
            )
        except Exception as exc:
            logger.critical(f"SQLite fallback failed: {exc}")
            self.mode = DatabaseMode.UNAVAILABLE

    async def close(self) -> None:
        """Gracefully close all database connections."""
        if self._pg_pool:
            await self._pg_pool.close()
            logger.info("PostgreSQL pool closed")

    # ─── Schema initialisation ────────────────────────────────────────────────

    async def _init_postgres_schema(self) -> None:
        """Run the PostgreSQL DDL schema. Uses IF NOT EXISTS — safe to run repeatedly."""
        async with self._pg_pool.acquire() as conn:
            await conn.execute(POSTGRES_SCHEMA)

    async def _init_sqlite_schema(self) -> None:
        """Run the SQLite DDL schema. Uses IF NOT EXISTS — safe to run repeatedly."""
        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.executescript(SQLITE_SCHEMA)
            await db.commit()

    # ─── Query Interface ──────────────────────────────────────────────────────

    async def fetchrow(
        self,
        query: str,
        *args: Any,
        sqlite_query: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Fetch a single row as a dict, or None if not found.

        Args:
            query:        PostgreSQL query with $1, $2... placeholders.
            *args:        Query arguments.
            sqlite_query: Optional SQLite-specific query (? placeholders).
                          Falls back to `query` if not provided.
        """
        if self.mode == DatabaseMode.POSTGRES:
            async with self._pg_pool.acquire() as conn:
                row = await conn.fetchrow(query, *args)
                return dict(row) if row else None

        if self.mode == DatabaseMode.SQLITE:
            q = sqlite_query or query
            async with self._sqlite_lock:
                async with aiosqlite.connect(self._sqlite_path) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute(q, args) as cursor:
                        row = await cursor.fetchone()
                        return dict(row) if row else None

        return None

    async def fetch(
        self,
        query: str,
        *args: Any,
        sqlite_query: Optional[str] = None,
    ) -> list[dict]:
        """
        Fetch multiple rows as a list of dicts.
        Returns an empty list if nothing found or DB unavailable.
        """
        if self.mode == DatabaseMode.POSTGRES:
            async with self._pg_pool.acquire() as conn:
                rows = await conn.fetch(query, *args)
                return [dict(r) for r in rows]

        if self.mode == DatabaseMode.SQLITE:
            q = sqlite_query or query
            async with self._sqlite_lock:
                async with aiosqlite.connect(self._sqlite_path) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute(q, args) as cursor:
                        rows = await cursor.fetchall()
                        return [dict(r) for r in rows]

        return []

    async def execute(
        self,
        query: Optional[str],
        *args: Any,
        sqlite_query: Optional[str] = None,
    ) -> Optional[str]:
        """
        Execute a write query (INSERT, UPDATE, DELETE).
        Returns the status string from PostgreSQL, or 'OK' from SQLite.
        Returns None if the database is unavailable.
        """
        if self.mode == DatabaseMode.POSTGRES:
            async with self._pg_pool.acquire() as conn:
                return await conn.execute(query, *args)

        if self.mode == DatabaseMode.SQLITE:
            q = sqlite_query or query
            if q is None:
                return None
            async with self._sqlite_lock:
                async with aiosqlite.connect(self._sqlite_path) as db:
                    await db.execute(q, args)
                    await db.commit()
                    return "OK"

        return None

    async def fetchval(
        self,
        query: str,
        *args: Any,
        sqlite_query: Optional[str] = None,
        column: int = 0,
    ) -> Any:
        """Fetch a single scalar value from the first row."""
        if self.mode == DatabaseMode.POSTGRES:
            async with self._pg_pool.acquire() as conn:
                return await conn.fetchval(query, *args, column=column)

        if self.mode == DatabaseMode.SQLITE:
            q = sqlite_query or query
            async with self._sqlite_lock:
                async with aiosqlite.connect(self._sqlite_path) as db:
                    async with db.execute(q, args) as cursor:
                        row = await cursor.fetchone()
                        return row[column] if row else None

        return None

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator:
        """
        Async context manager for a database transaction.
        Only fully supported on PostgreSQL — SQLite yields None.
        """
        if self.mode == DatabaseMode.POSTGRES:
            async with self._pg_pool.acquire() as conn:
                async with conn.transaction():
                    yield conn
        else:
            yield None

    # ─── Status helpers ───────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Return True if any database connection is active."""
        return self.mode != DatabaseMode.UNAVAILABLE

    def is_postgres(self) -> bool:
        """Return True if running on PostgreSQL."""
        return self.mode == DatabaseMode.POSTGRES

    def is_sqlite(self) -> bool:
        """Return True if running on the SQLite fallback."""
        return self.mode == DatabaseMode.SQLITE

    def __repr__(self) -> str:
        return f"DatabaseConnection(mode={self.mode.name})"
