import uuid
import logging
import asyncpg
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from database.connection import db

logger = logging.getLogger("Klaud.LicenseManager")

class LicenseManager:
    """
    KLAUD-NINJA GATEKEEPER.
    Strictly enforces that NO features run without a valid, paid license.
    """
    
    @staticmethod
    async def ensure_db():
        if db.pool is None:
            await db.connect()

    @staticmethod
    async def has_access(guild_id: Optional[int]) -> bool:
        """
        The absolute gate. If this returns False, the bot is a brick in that guild.
        """
        if not guild_id:
            return False
            
        await LicenseManager.ensure_db()
        try:
            row = await db.fetchrow(
                "SELECT active, expires_at FROM licenses WHERE server_id = $1", 
                guild_id
            )
            if not row or not row['active']:
                return False
            
            # Check for expiration (2026 timezone-aware handling)
            return row['expires_at'] > datetime.now()
        except Exception as e:
            logger.error(f"License verification failure: {e}")
            return False

    @staticmethod
    async def require_paid(guild_id: Optional[int]) -> bool:
        """
        Since there is no free tier, this is synonymous with has_access.
        Maintained for backward compatibility with existing cog calls.
        """
        return await LicenseManager.has_access(guild_id)

    @staticmethod
    async def generate_license(mode: str, duration_days: int, creator_id: int) -> str:
        await LicenseManager.ensure_db()
        new_key = f"KLAUD-{uuid.uuid4().hex[:16].upper()}"
        try:
            await db.execute(
                """INSERT INTO pending_licenses (license_key, mode, duration_days, created_by) 
                   VALUES ($1, $2, $3, $4)""",
                new_key, mode.lower(), duration_days, creator_id
            )
            return new_key
        except Exception as e:
            logger.error(f"Failed to generate license: {e}")
            raise e

    @staticmethod
    async def redeem_license(guild_id: int, key: str, user_id: int) -> Dict[str, Any]:
        await LicenseManager.ensure_db()
        async with db.pool.acquire() as conn:
            async with conn.transaction():
                pending = await conn.fetchrow(
                    "SELECT mode, duration_days FROM pending_licenses WHERE license_key = $1", 
                    key
                )
                if not pending:
                    return {"success": False, "message": "Key invalid or already used."}
                
                expiry = datetime.now() + timedelta(days=pending['duration_days'])
                await conn.execute(
                    """INSERT INTO licenses (server_id, license_key, mode, expires_at, active, activated_at, owner_id) 
                       VALUES ($1, $2, $3, $4, $5, $6, $7)
                       ON CONFLICT (server_id) DO UPDATE SET 
                       license_key = $2, mode = $3, expires_at = $4, active = $5, activated_at = $6, owner_id = $7""",
                    guild_id, key, pending['mode'], expiry, True, datetime.now(), user_id
                )
                await conn.execute("DELETE FROM pending_licenses WHERE license_key = $1", key)
                return {"success": True, "mode": pending['mode'], "expiry": expiry}
