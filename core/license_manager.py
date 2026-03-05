"""
KLAUD-NINJA — License Manager
Central authority for all license operations.
No guild may use the bot without passing through this manager.

Key format:   KLAUD-XXXX-XXXX-XXXX  (cryptographically secure)
Tiers:        BASIC | PRO | ENTERPRISE
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from database.connection import DatabaseConnection

logger = logging.getLogger("klaud.license_manager")

VALID_TIERS = frozenset({"BASIC", "PRO", "ENTERPRISE"})
KEY_PATTERN = re.compile(r"^KLAUD-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$")


@dataclass
class LicenseRecord:
    id: int
    license_key: str
    server_id: Optional[int]
    owner_id: Optional[int]
    tier: str
    created_at: datetime
    expires_at: Optional[datetime]
    active: bool
    redeemed_at: Optional[datetime]
    redeemed_by: Optional[int]

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at.replace(tzinfo=None) if self.expires_at.tzinfo else datetime.utcnow() > self.expires_at

    @property
    def is_valid(self) -> bool:
        return self.active and not self.is_expired and self.server_id is not None


@dataclass
class LicenseCacheEntry:
    valid: bool
    tier: str
    record: Optional[LicenseRecord]
    cached_at: float
    ttl: float = 300.0

    @property
    def is_stale(self) -> bool:
        return (time.monotonic() - self.cached_at) > self.ttl


class LicenseManager:
    UNLICENSED_MESSAGE = (
        "⛔ **This server is not authorized to use Klaud.**\n"
        "Redeem a valid license key with `/license redeem <key>` to activate."
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
        self._cache: dict[int, LicenseCacheEntry] = {}
        self._cache_lock = asyncio.Lock()

    async def is_licensed(self, guild_id: int) -> bool:
        if self._owner_test_server_id and guild_id == self._owner_test_server_id:
            return True

        entry = await self._get_cache(guild_id)
        if entry and not entry.is_stale:
            return entry.valid

        record = await self._fetch_license(guild_id)
        valid = record is not None and record.is_valid
        tier = record.tier if record else "NONE"
        await self._set_cache(guild_id, valid, tier, record)
        return valid

    async def get_tier(self, guild_id: int) -> str:
        entry = await self._get_cache(guild_id)
        if entry and not entry.is_stale:
            return entry.tier if entry.valid else "NONE"

        record = await self._fetch_license(guild_id)
        if record and record.is_valid:
            return record.tier
        return "NONE"

    async def get_record(self, guild_id: int) -> Optional[LicenseRecord]:
        return await self._fetch_license(guild_id)

    async def redeem_key(
        self,
        key: str,
        guild_id: int,
        redeemed_by: int,
    ) -> tuple[bool, str]:
        key = key.strip().upper()

        if not KEY_PATTERN.match(key):
            return False, "❌ Invalid key format. Keys look like `KLAUD-XXXX-XXXX-XXXX`."

        existing = await self._fetch_license(guild_id)
        if existing and existing.is_valid:
            return False, f"❌ This server already has an active **{existing.tier}** license."

        row = await self._db.fetchrow(
            "SELECT * FROM licenses WHERE license_key = $1",
            key,
            sqlite_query="SELECT * FROM licenses WHERE license_key = ?",
        )

        if not row:
            return False, "❌ License key not found. Check for typos."

        if row["server_id"] is not None:
            if row["server_id"] == guild_id:
                return False, "❌ This key is already bound to this server."
            return False, "❌ This key has already been redeemed by another server."

        if not row["active"]:
            return False, "❌ This license key has been disabled."

        if row["expires_at"] is not None:
            exp = row["expires_at"]
            if isinstance(exp, str):
                exp = datetime.fromisoformat(exp)
            exp_naive = exp.replace(tzinfo=None)
            if datetime.utcnow() > exp_naive:
                return False, "❌ This license key has expired."

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

        tier = row["tier"]
        return True, f"✅ License redeemed! This server now has an active **{tier}** license."

    async def generate_key(
        self,
        tier: str,
        duration_days: Optional[int],
        created_by: int,
    ) -> str:
        if tier.upper() not in VALID_TIERS:
            raise ValueError(f"Invalid tier '{tier}'. Must be one of: {', '.join(VALID_TIERS)}")

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
            key, tier.upper(), created_by, now, expires_at,
        )

        logger.info(
            f"License generated | key={key} | tier={tier} | "
            f"expires={'never' if expires_at is None else expires_at.date()} | by={created_by}"
        )
        return key

    async def revoke_key(self, key: str) -> tuple[bool, str]:
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

        if row["server_id"]:
            await self._invalidate_cache(int(row["server_id"]))

        logger.info(f"License revoked | key={key}")
        return True, f"✅ License `{key}` has been revoked."

    async def disable_server(self, guild_id: int) -> tuple[bool, str]:
        result = await self._db.execute(
            "UPDATE licenses SET active = FALSE WHERE server_id = $1 AND active = TRUE",
            guild_id,
            sqlite_query="UPDATE licenses SET active = 0 WHERE server_id = ? AND active = 1",
        )

        await self._invalidate_cache(guild_id)

        if result and result != "UPDATE 0":
            return True, f"✅ License for server `{guild_id}` has been disabled."
        return False, "❌ No active license found for that server."

    async def list_licenses(
        self,
        active_only: bool = True,
        limit: int = 20,
    ) -> list[dict]:
        if self._db.is_postgres():
            query = (
                "SELECT license_key, server_id, tier, active, expires_at, redeemed_at "
                "FROM licenses "
                + ("WHERE active = TRUE " if active_only else "")
                + "ORDER BY created_at DESC LIMIT $1"
            )
            return await self._db.fetch(query, limit)
        else:
            query = (
                "SELECT license_key, server_id, tier, active, expires_at, redeemed_at "
                "FROM licenses "
                + ("WHERE active = 1 " if active_only else "")
                + "ORDER BY created_at DESC LIMIT ?"
            )
            return await self._db.fetch(query, limit, sqlite_query=query)

    def _generate_secure_key(self) -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        parts = []
        for _ in range(3):
            segment = "".join(secrets.choice(alphabet) for _ in range(4))
            parts.append(segment)
        return "KLAUD-" + "-".join(parts)

    async def _fetch_license(self, guild_id: int) -> Optional[LicenseRecord]:
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

        except Exception as e:
            logger.error(f"Error fetching license for guild {guild_id}: {e}")
            return None

    def _row_to_record(self, row: dict) -> LicenseRecord:
        def _parse_dt(val) -> Optional[datetime]:
            if val is None:
                return None
            if isinstance(val, datetime):
                return val.replace(tzinfo=None)
            if isinstance(val, str):
                dt = datetime.fromisoformat(val)
                return dt.replace(tzinfo=None)
            return None

        return LicenseRecord(
            id=row["id"],
            license_key=row["license_key"],
            server_id=row.get("server_id"),
            owner_id=row.get("owner_id"),
            tier=row.get("tier", "BASIC"),
            created_at=_parse_dt(row.get("created_at")) or datetime.utcnow(),
            expires_at=_parse_dt(row.get("expires_at")),
            active=bool(row.get("active", False)),
            redeemed_at=_parse_dt(row.get("redeemed_at")),
            redeemed_by=row.get("redeemed_by"),
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

    async def purge_expired_cache(self) -> int:
        async with self._cache_lock:
            stale = [gid for gid, e in self._cache.items() if e.is_stale]
            for gid in stale:
                del self._cache[gid]
            return len(stale)
