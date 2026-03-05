"""
KLAUD-NINJA — License Manager
═══════════════════════════════════════════════════════════════════════════════
Central authority for all license operations.
No guild may use ANY bot feature without passing through this manager.

Key format:  KLAUD-XXXX-XXXX-XXXX  (cryptographically secure, alphabet avoids
             ambiguous characters like 0/O, 1/I)
Tiers:       BASIC | PRO | ENTERPRISE

Architecture:
  • In-memory TTL cache per guild_id prevents DB hit on every message
  • Cache is invalidated immediately on any license change
  • All datetime operations use naive UTC to avoid timezone confusion
  • Owner's test server always passes without a DB check
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from database.connection import DatabaseConnection

logger = logging.getLogger("klaud.license_manager")

# ─── Constants ────────────────────────────────────────────────────────────────

VALID_TIERS = frozenset({"BASIC", "PRO", "ENTERPRISE"})

# Format: KLAUD-XXXX-XXXX-XXXX where X is alphanumeric (no ambiguous chars)
KEY_PATTERN = re.compile(r"^KLAUD-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$")

# Alphabet for key generation — avoids 0/O and 1/I confusion
_KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


# ─── Data Models ─────────────────────────────────────────────────────────────

@dataclass
class LicenseRecord:
    """
    Represents a fully-loaded license row from the database.
    All datetimes are naive UTC.
    """
    id:          int
    license_key: str
    server_id:   Optional[int]
    owner_id:    Optional[int]
    tier:        str
    created_at:  datetime
    expires_at:  Optional[datetime]
    active:      bool
    redeemed_at: Optional[datetime]
    redeemed_by: Optional[int]

    @property
    def is_expired(self) -> bool:
        """True if the license has passed its expiry date."""
        if self.expires_at is None:
            return False
        # Use naive UTC comparison — all datetimes stored as naive UTC
        exp = self.expires_at.replace(tzinfo=None) if self.expires_at.tzinfo else self.expires_at
        return datetime.utcnow() > exp

    @property
    def is_valid(self) -> bool:
        """True if the license is active, not expired, and bound to a server."""
        return (
            self.active
            and not self.is_expired
            and self.server_id is not None
        )

    @property
    def days_remaining(self) -> Optional[int]:
        """Returns days until expiry, or None for lifetime licenses."""
        if self.expires_at is None:
            return None
        exp = self.expires_at.replace(tzinfo=None)
        delta = exp - datetime.utcnow()
        return max(0, delta.days)

    def __str__(self) -> str:
        exp = f"expires {self.expires_at.date()}" if self.expires_at else "lifetime"
        return f"LicenseRecord(key={self.license_key}, tier={self.tier}, {exp}, valid={self.is_valid})"


@dataclass
class LicenseCacheEntry:
    """TTL-based in-memory cache entry for license validation results."""
    valid:     bool
    tier:      str
    record:    Optional[LicenseRecord]
    cached_at: float   # time.monotonic() timestamp
    ttl:       float = 300.0

    @property
    def is_stale(self) -> bool:
        """True if this cache entry has exceeded its TTL."""
        return (time.monotonic() - self.cached_at) > self.ttl


# ─── License Manager ─────────────────────────────────────────────────────────

class LicenseManager:
    """
    Manages all license lifecycle: generation, redemption, validation,
    revocation, disabling, and cache management.

    Thread-safe: uses asyncio.Lock for cache access.
    All public methods are async-safe and can be called from any cog.
    """

    UNLICENSED_MESSAGE = (
        "⛔ **This server is not authorized to use Klaud.**\n"
        "Redeem a valid license key with `/license redeem <key>` to activate.\n"
        "Need a license? Contact the bot owner."
    )

    def __init__(
        self,
        db: DatabaseConnection,
        owner_id: int,
        license_secret: str,
        cache_ttl: float = 300.0,
        owner_test_server_id: Optional[int] = None,
    ) -> None:
        self._db = db
        self._owner_id = owner_id
        self._secret = license_secret or secrets.token_hex(32)
        self._cache_ttl = cache_ttl
        self._owner_test_server_id = owner_test_server_id

        # guild_id → LicenseCacheEntry
        self._cache: dict[int, LicenseCacheEntry] = {}
        self._cache_lock = asyncio.Lock()

    # ─── Primary validation ───────────────────────────────────────────────────

    async def is_licensed(self, guild_id: int) -> bool:
        """
        Return True if the guild has a valid, unexpired license.

        This is the hot path — called on every message event.
        Results are cached for cache_ttl seconds to avoid DB hits.
        Owner's test server always returns True without any DB check.
        """
        # Owner test server — always licensed
        if self._owner_test_server_id and guild_id == self._owner_test_server_id:
            return True

        # Check memory cache first
        entry = await self._get_cache(guild_id)
        if entry is not None and not entry.is_stale:
            return entry.valid

        # Cache miss or stale — query the database
        record = await self._fetch_license(guild_id)
        valid = record is not None and record.is_valid
        tier  = record.tier if record else "NONE"

        await self._set_cache(guild_id, valid, tier, record)
        return valid

    async def get_tier(self, guild_id: int) -> str:
        """
        Return the license tier for a guild.
        Returns 'NONE' if unlicensed or expired.
        """
        # Check cache
        entry = await self._get_cache(guild_id)
        if entry is not None and not entry.is_stale:
            return entry.tier if entry.valid else "NONE"

        record = await self._fetch_license(guild_id)
        if record and record.is_valid:
            await self._set_cache(guild_id, True, record.tier, record)
            return record.tier

        await self._set_cache(guild_id, False, "NONE", record)
        return "NONE"

    async def get_record(self, guild_id: int) -> Optional[LicenseRecord]:
        """Return the full LicenseRecord for a guild, or None if not found."""
        return await self._fetch_license(guild_id)

    # ─── Key operations ───────────────────────────────────────────────────────

    async def redeem_key(
        self,
        key: str,
        guild_id: int,
        redeemed_by: int,
    ) -> tuple[bool, str]:
        """
        Attempt to redeem a license key for a guild.

        Returns:
            (True, success_message) on success.
            (False, error_message) on failure.

        Failure reasons:
          - Invalid key format
          - Key not found in DB
          - Key already redeemed by this or another server
          - Key disabled
          - Key expired
          - Guild already has an active license
        """
        key = key.strip().upper()

        # Validate format
        if not KEY_PATTERN.match(key):
            return False, (
                "❌ Invalid key format.\n"
                "License keys look like: `KLAUD-XXXX-XXXX-XXXX`"
            )

        # Check if guild already has an active license
        existing = await self._fetch_license(guild_id)
        if existing and existing.is_valid:
            return False, (
                f"❌ This server already has an active **{existing.tier}** license.\n"
                "Use `/license status` to view it."
            )

        # Fetch the key from database
        row = await self._db.fetchrow(
            "SELECT * FROM licenses WHERE license_key = $1",
            key,
            sqlite_query="SELECT * FROM licenses WHERE license_key = ?",
        )

        if not row:
            return False, "❌ License key not found. Please check for typos."

        # Check if already bound to a server
        if row.get("server_id") is not None:
            if int(row["server_id"]) == guild_id:
                return False, "❌ This key is already bound to this server."
            return False, "❌ This key has already been redeemed by another server."

        # Check if active
        if not row.get("active"):
            return False, "❌ This license key has been deactivated."

        # Check expiry
        if row.get("expires_at") is not None:
            exp = row["expires_at"]
            if isinstance(exp, str):
                exp = datetime.fromisoformat(exp)
            exp_naive = exp.replace(tzinfo=None)
            if datetime.utcnow() > exp_naive:
                return False, "❌ This license key has expired."

        # Bind the key to this guild
        now = datetime.utcnow()
        await self._db.execute(
            """
            UPDATE licenses
            SET server_id = $1, redeemed_at = $2, redeemed_by = $3
            WHERE license_key = $4
            """,
            guild_id, now, redeemed_by, key,
            sqlite_query=(
                "UPDATE licenses "
                "SET server_id = ?, redeemed_at = ?, redeemed_by = ? "
                "WHERE license_key = ?"
            ),
        )

        await self._invalidate_cache(guild_id)

        tier = row.get("tier", "BASIC")
        logger.info(
            f"License redeemed | key={key} | guild={guild_id} | "
            f"tier={tier} | by={redeemed_by}"
        )
        return True, f"✅ License redeemed! This server now has an active **{tier}** license."

    async def generate_key(
        self,
        tier: str,
        duration_days: Optional[int],
        created_by: int,
    ) -> str:
        """
        Generate a new license key and persist it to the database.
        The key is NOT yet bound to any server — it must be redeemed.

        Args:
            tier:          BASIC | PRO | ENTERPRISE
            duration_days: Days until expiry. None or 0 = lifetime.
            created_by:    Discord user ID of the key creator (should be owner).

        Returns:
            The generated key string.

        Raises:
            ValueError if tier is invalid.
        """
        tier = tier.strip().upper()
        if tier not in VALID_TIERS:
            raise ValueError(
                f"Invalid tier '{tier}'. "
                f"Must be one of: {', '.join(sorted(VALID_TIERS))}"
            )

        key = self._generate_secure_key()
        now = datetime.utcnow()
        expires_at: Optional[datetime] = None

        if duration_days is not None and duration_days > 0:
            expires_at = datetime.utcnow() + timedelta(days=duration_days)

        await self._db.execute(
            """
            INSERT INTO licenses (license_key, tier, owner_id, created_at, expires_at, active)
            VALUES ($1, $2, $3, $4, $5, TRUE)
            """,
            key, tier, created_by, now, expires_at,
            sqlite_query=(
                "INSERT INTO licenses "
                "(license_key, tier, owner_id, created_at, expires_at, active) "
                "VALUES (?, ?, ?, ?, ?, 1)"
            ),
        )

        exp_str = expires_at.date().isoformat() if expires_at else "never"
        logger.info(
            f"License generated | key={key} | tier={tier} | "
            f"expires={exp_str} | by={created_by}"
        )
        return key

    async def activate_server(
        self,
        guild_id: int,
        tier: str,
        duration_days: Optional[int],
        activated_by: int,
    ) -> tuple[bool, str, Optional[str]]:
        """
        Instantly activate a server without requiring key redemption.
        Generates a key and immediately binds it to the server.

        Returns:
            (success, message, key_string_or_None)
        """
        # Check if already licensed
        existing = await self._fetch_license(guild_id)
        if existing and existing.is_valid:
            return False, f"❌ Server `{guild_id}` already has an active **{existing.tier}** license.", None

        try:
            key = await self.generate_key(
                tier=tier,
                duration_days=duration_days,
                created_by=activated_by,
            )
        except ValueError as exc:
            return False, f"❌ {exc}", None

        # Directly bind it
        now = datetime.utcnow()
        await self._db.execute(
            """
            UPDATE licenses
            SET server_id = $1, redeemed_at = $2, redeemed_by = $3
            WHERE license_key = $4
            """,
            guild_id, now, activated_by, key,
            sqlite_query=(
                "UPDATE licenses "
                "SET server_id = ?, redeemed_at = ?, redeemed_by = ? "
                "WHERE license_key = ?"
            ),
        )

        await self._invalidate_cache(guild_id)

        logger.info(
            f"Server auto-activated | guild={guild_id} | tier={tier} | "
            f"key={key} | by={activated_by}"
        )
        return True, f"✅ Server `{guild_id}` activated with **{tier}** license.", key

    async def revoke_key(self, key: str) -> tuple[bool, str]:
        """Revoke a license key by its key string. Disables it immediately."""
        key = key.strip().upper()

        row = await self._db.fetchrow(
            "SELECT * FROM licenses WHERE license_key = $1",
            key,
            sqlite_query="SELECT * FROM licenses WHERE license_key = ?",
        )

        if not row:
            return False, "❌ Key not found."

        await self._db.execute(
            "UPDATE licenses SET active = FALSE WHERE license_key = $1",
            key,
            sqlite_query="UPDATE licenses SET active = 0 WHERE license_key = ?",
        )

        if row.get("server_id"):
            await self._invalidate_cache(int(row["server_id"]))

        logger.info(f"License revoked | key={key}")
        return True, f"✅ License `{key}` has been revoked."

    async def disable_server(self, guild_id: int) -> tuple[bool, str]:
        """Disable all active licenses for a specific server."""
        result = await self._db.execute(
            "UPDATE licenses SET active = FALSE WHERE server_id = $1 AND active = TRUE",
            guild_id,
            sqlite_query="UPDATE licenses SET active = 0 WHERE server_id = ? AND active = 1",
        )

        await self._invalidate_cache(guild_id)

        if result and result not in ("UPDATE 0", "OK"):
            return True, f"✅ License for server `{guild_id}` has been disabled."
        # For SQLite 'OK' always returned — check differently
        if self._db.is_sqlite():
            return True, f"✅ Attempted to disable license for server `{guild_id}`."
        return False, "❌ No active license found for that server."

    async def list_licenses(
        self,
        active_only: bool = True,
        limit: int = 20,
    ) -> list[dict]:
        """
        List licenses from the database.
        Owner-only operation.
        """
        if self._db.is_postgres():
            where = "WHERE active = TRUE " if active_only else ""
            query = (
                "SELECT license_key, server_id, tier, active, expires_at, redeemed_at, created_at "
                f"FROM licenses {where}"
                "ORDER BY created_at DESC LIMIT $1"
            )
            return await self._db.fetch(query, limit)
        else:
            where = "WHERE active = 1 " if active_only else ""
            query = (
                "SELECT license_key, server_id, tier, active, expires_at, redeemed_at, created_at "
                f"FROM licenses {where}"
                "ORDER BY created_at DESC LIMIT ?"
            )
            return await self._db.fetch(query, limit, sqlite_query=query)

    # ─── Cache management ─────────────────────────────────────────────────────

    async def purge_expired_cache(self) -> int:
        """Remove stale entries from the in-memory cache. Returns count removed."""
        async with self._cache_lock:
            stale_ids = [gid for gid, e in self._cache.items() if e.is_stale]
            for gid in stale_ids:
                del self._cache[gid]
            return len(stale_ids)

    async def force_refresh(self, guild_id: int) -> None:
        """Force the cache to refresh for a specific guild on next access."""
        await self._invalidate_cache(guild_id)

    # ─── Private helpers ──────────────────────────────────────────────────────

    def _generate_secure_key(self) -> str:
        """
        Generate a cryptographically secure license key.
        Format: KLAUD-XXXX-XXXX-XXXX
        Uses secrets.choice for cryptographic randomness.
        """
        parts = [
            "".join(secrets.choice(_KEY_ALPHABET) for _ in range(4))
            for _ in range(3)
        ]
        return "KLAUD-" + "-".join(parts)

    async def _fetch_license(self, guild_id: int) -> Optional[LicenseRecord]:
        """Fetch the most recent license record for a guild from the database."""
        try:
            if self._db.is_postgres():
                row = await self._db.fetchrow(
                    """
                    SELECT * FROM licenses
                    WHERE server_id = $1
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    guild_id,
                )
            else:
                row = await self._db.fetchrow(
                    None,
                    guild_id,
                    sqlite_query=(
                        "SELECT * FROM licenses WHERE server_id = ? "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                )

            if not row:
                return None

            return self._row_to_record(row)

        except Exception as exc:
            logger.error(f"Error fetching license for guild {guild_id}: {exc}")
            return None

    def _row_to_record(self, row: dict) -> LicenseRecord:
        """Convert a database row dict into a LicenseRecord dataclass."""

        def _parse_dt(val) -> Optional[datetime]:
            if val is None:
                return None
            if isinstance(val, datetime):
                return val.replace(tzinfo=None)
            if isinstance(val, str):
                try:
                    dt = datetime.fromisoformat(val)
                    return dt.replace(tzinfo=None)
                except ValueError:
                    return None
            return None

        return LicenseRecord(
            id          = int(row.get("id", 0)),
            license_key = str(row.get("license_key", "")),
            server_id   = int(row["server_id"]) if row.get("server_id") else None,
            owner_id    = int(row["owner_id"])  if row.get("owner_id")  else None,
            tier        = str(row.get("tier", "BASIC")).upper(),
            created_at  = _parse_dt(row.get("created_at")) or datetime.utcnow(),
            expires_at  = _parse_dt(row.get("expires_at")),
            active      = bool(row.get("active", False)),
            redeemed_at = _parse_dt(row.get("redeemed_at")),
            redeemed_by = int(row["redeemed_by"]) if row.get("redeemed_by") else None,
        )

    async def _get_cache(self, guild_id: int) -> Optional[LicenseCacheEntry]:
        async with self._cache_lock:
            return self._cache.get(guild_id)

    async def _set_cache(
        self,
        guild_id: int,
        valid: bool,
        tier: str,
        record: Optional[LicenseRecord],
    ) -> None:
        async with self._cache_lock:
            self._cache[guild_id] = LicenseCacheEntry(
                valid=valid,
                tier=tier,
                record=record,
                cached_at=time.monotonic(),
                ttl=self._cache_ttl,
            )

    async def _invalidate_cache(self, guild_id: int) -> None:
        async with self._cache_lock:
            self._cache.pop(guild_id, None)

    def __repr__(self) -> str:
        return (
            f"LicenseManager("
            f"cache_size={len(self._cache)}, "
            f"owner={self._owner_id})"
        )
