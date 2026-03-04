import uuid
from datetime import datetime, timedelta
from database.connection import db

class LicenseManager:
    @staticmethod
    async def ensure_db():
        if db.pool is None:
            await db.connect()

    @staticmethod
    async def has_access(guild_id: int):
        """Standard check for moderation."""
        if not guild_id: return False
        await LicenseManager.ensure_db()
        try:
            async with db.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT active, expires_at FROM licenses WHERE server_id = $1", 
                    guild_id
                )
                if row and row['active'] and row['expires_at'] > datetime.now():
                    return True
                return False
        except Exception:
            return False

    @staticmethod
    async def require_paid(guild_id: int):
        if not guild_id: return False
        await LicenseManager.ensure_db()
        async with db.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT mode, active, expires_at FROM licenses WHERE server_id = $1", 
                guild_id
            )
            return row and row['active'] and row['mode'] == 'paid' and row['expires_at'] > datetime.now()

    @staticmethod
    async def generate_license(mode: str, duration_days: int):
        """Generates a key that isn't tied to a server YET."""
        await LicenseManager.ensure_db()
        new_key = f"KLAUD-{uuid.uuid4().hex[:12].upper()}"
        expiry = datetime.now() + timedelta(days=duration_days)
        
        async with db.pool.acquire() as conn:
            # We use 0 as a placeholder server_id for 'unclaimed' keys
            # or we allow server_id to be NULL if your DB schema allows it.
            # To be safe, we'll just store the key.
            await conn.execute(
                """INSERT INTO licenses (server_id, license_key, mode, expires_at, active) 
                   VALUES ($1, $2, $3, $4, $5)""",
                # We use a random large number for unclaimed keys to avoid PK errors
                uuid.uuid4().int >> 64, new_key, mode.lower(), expiry, False
            )
        return new_key
