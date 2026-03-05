"""
KLAUD-NINJA — Database Connection Layer
Manages PostgreSQL (primary) and SQLite (fallback) connections.
Provides a unified async interface used by all services.
Schema creation is handled here on first connection.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from enum import Enum, auto
from typing import Any, AsyncGenerator, Optional, Union

import aiosqlite

try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False

logger = logging.getLogger("klaud.database")

# ─── Schema ──────────────────────────────────────────────────────────────────

POSTGRES_SCHEMA = """
-- License keys
CREATE TABLE IF NOT EXISTS licenses (
    id              SERIAL PRIMARY KEY,
    license_key     TEXT NOT NULL UNIQUE,
    server_id       BIGINT,
    owner_id        BIGINT,
    tier            TEXT NOT NULL DEFAULT 'BASIC',   -- BASIC | PRO | ENTERPRISE
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    redeemed_at     TIMESTAMPTZ,
    redeemed_by     BIGINT
);

-- Per-guild settings
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id                BIGINT PRIMARY KEY,
    mod_intensity           TEXT NOT NULL DEFAULT 'MEDIUM',  -- LOW | MEDIUM | HIGH | EXTREME
    mod_log_channel_id      BIGINT,
    verification_channel_id BIGINT,
    verification_role_id    BIGINT,
    welcome_channel_id      BIGINT,
    ai_admin_enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Moderation action log
CREATE TABLE IF NOT EXISTS mod_actions (
    id              SERIAL PRIMARY KEY,
    guild_id        BIGINT NOT NULL,
    user_id         BIGINT NOT NULL,
    moderator_id    BIGINT NOT NULL,   -- 0 = AI auto-mod
    action          TEXT NOT NULL,     -- warn | delete | timeout | kick | ban
    reason          TEXT,
    message_content TEXT,
    channel_id      BIGINT,
    duration_secs   INT,
    ai_confidence   REAL,
    ai_categories   TEXT[],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Punishment / warning counters per user per guild
CREATE TABLE IF NOT EXISTS user_punishments (
    id          SERIAL PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    warn_count  INT NOT NULL DEFAULT 0,
    last_action TEXT,
    last_at     TIMESTAMPTZ,
    UNIQUE(guild_id, user_id)
);

-- Audit log for AI admin actions
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

-- Indexes
CREATE INDEX IF NOT EXISTS idx_licenses_server    ON licenses(server_id);
CREATE INDEX IF NOT EXISTS idx_licenses_key       ON licenses(license_key);
CREATE INDEX IF NOT EXISTS idx_mod_actions_guild  ON mod_actions(guild_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mod_actions_user   ON mod_actions(guild_id, user_id);
CREATE INDEX IF NOT EXISTS idx_audit_guild        ON audit_log(guild_id, created_at DESC);
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


class DatabaseMode(Enum):
    POSTGRES = auto()
    SQLITE = auto()
    UNAVAILABLE = auto()


class DatabaseConnection:
    """
    Unified async database interface.
    Tries PostgreSQL first; falls back to SQLite if PG is unreachable.
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
        self._sqlite_lock: asyncio.Lock = asyncio.Lock()
        self.mode: DatabaseMode = DatabaseMode.UNAVAILABLE

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Establish database connection. Call once at startup."""
        if self._database_url and HAS_ASYNCPG:
            try:
                logger.info("Connecting to PostgreSQL...")
                self._pg_pool = await asyncpg.create_pool(
                    dsn=self._database_url,
                    min_size=self._pool_min,
                    max_size=self._pool_max,
                    command_timeout=30,
                    statement_cache_size=100,
                )
                await self._init_postgres_schema()
                self.mode = DatabaseMode.POSTGRES
                logger.info("PostgreSQL connected and schema initialised")
                return
            except Exception as e:
                logger.error(f"PostgreSQL connection failed: {e}")
                logger.warning("Falling back to SQLite")

        # SQLite fallback
        try:
            os.makedirs(os.path.dirname(self._sqlite_path) or ".", exist_ok=True)
            await self._init_sqlite_schema()
            self.mode = DatabaseMode.SQLITE
            logger.warning(
                f"Running on SQLite fallback at {self._sqlite_path}. "
                "All unlicensed guilds will be denied."
            )
        except Exception as e:
            logger.critical(f"SQLite fallback also failed: {e}")
            self.mode = DatabaseMode.UNAVAILABLE

    async def close(self) -> None:
        """Gracefully close all connections."""
        if self._pg_pool:
            await self._pg_pool.close()
            logger.info("PostgreSQL pool closed")

    # ─── Schema init ─────────────────────────────────────────────────────────

    async def _init_postgres_schema(self) -> None:
        async with self._pg_pool.acquire() as conn:
            await conn.execute(POSTGRES_SCHEMA)

    async def _init_sqlite_schema(self) -> None:
        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.executescript(SQLITE_SCHEMA)
            await db.commit()

    # ─── Query Execution ─────────────────────────────────────────────────────

    async def fetchrow(
        self, query: str, *args: Any, sqlite_query: Optional[str] = None
    ) -> Optional[dict]:
        """Fetch a single row as a dict, or None if not found."""
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
        self, query: str, *args: Any, sqlite_query: Optional[str] = None
    ) -> list[dict]:
        """Fetch multiple rows as a list of dicts."""
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
        query: str,
        *args: Any,
        sqlite_query: Optional[str] = None,
    ) -> Optional[str]:
        """Execute a write query. Returns status string or None."""
        if self.mode == DatabaseMode.POSTGRES:
            async with self._pg_pool.acquire() as conn:
                return await conn.execute(query, *args)

        if self.mode == DatabaseMode.SQLITE:
            q = sqlite_query or query
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
        """Fetch a single scalar value."""
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
        """Async context manager for a database transaction (Postgres only)."""
        if self.mode == DatabaseMode.POSTGRES:
            async with self._pg_pool.acquire() as conn:
                async with conn.transaction():
                    yield conn
        else:
            # SQLite: yield None — callers must handle this gracefully
            yield None

    def is_available(self) -> bool:
        return self.mode != DatabaseMode.UNAVAILABLE

    def is_postgres(self) -> bool:
        return self.mode == DatabaseMode.POSTGRES

    def is_sqlite(self) -> bool:
        return self.mode == DatabaseMode.SQLITE
