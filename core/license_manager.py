import uuid
from datetime import datetime, timedelta
from database.connection import db # Ensure this points to your database folder

class LicenseManager:
    @staticmethod
    async def ensure_db():
        """Ensure the database is connected before running a query."""
        if db.pool is None:
            await db.connect()

    @staticmethod
    async def get_server_mode(guild_id: int):
        await LicenseManager.ensure_db()
        async with db.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT mode, expires_at, active FROM licenses WHERE server_id = $1", 
                guild_id
            )
            if row and row['active'] and row['expires_at'] > datetime.now():
                return row['mode']
            return "free"

    @staticmethod
    async def generate_license(mode: str, duration_days: int):
        await LicenseManager.ensure_db()
        key = f"KLAUD-{uuid.uuid4().hex[:12].upper()}"
        expires_at = datetime.now() + timedelta(days=duration_days)
        
        async with db.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO licenses (key, mode, expires_at, active) VALUES ($1, $2, $3, $4)",
                key, mode, expires_at, False
            )
        return key
