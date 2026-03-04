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
        """Checks if the server has any active license."""
        await LicenseManager.ensure_db()
        async with db.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT active, expires_at FROM licenses WHERE server_id = $1", 
                guild_id
            )
            if row and row['active'] and row['expires_at'] > datetime.now():
                return True
            return False

    @staticmethod
    async def require_paid(guild_id: int):
        """Checks if the server has a 'paid' tier license."""
        await LicenseManager.ensure_db()
        async with db.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT mode, active, expires_at FROM licenses WHERE server_id = $1", 
                guild_id
            )
            if row and row['active'] and row['mode'] == 'paid' and row['expires_at'] > datetime.now():
                return True
            return False

    @staticmethod
    async def generate_license(mode: str, duration_days: int):
        """Generates a new license key and saves it to the DB."""
        await LicenseManager.ensure_db()
        new_key = f"KLAUD-{uuid.uuid4().hex[:12].upper()}"
        expiry = datetime.now() + timedelta(days=duration_days)
        
        async with db.pool.acquire() as conn:
            # We use license_key to match the latest DB column fix
            await conn.execute(
                """INSERT INTO licenses (license_key, mode, expires_at, active) 
                   VALUES ($1, $2, $3, $4)""",
                new_key, mode.lower(), expiry, False
            )
        return new_key
