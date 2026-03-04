import uuid
import asyncpg
import os
from datetime import datetime, timedelta

class LicenseManager:
    @staticmethod
    async def get_conn():
        # This bypasses the 'pool' issue by creating a direct connection if needed
        return await asyncpg.connect(os.getenv("DATABASE_URL") + "?statement_cache_size=0")

    @staticmethod
    async def generate_license(mode: str, duration_days: int):
        conn = await LicenseManager.get_conn()
        try:
            key = f"KLAUD-{uuid.uuid4().hex[:12].upper()}"
            expires_at = datetime.now() + timedelta(days=duration_days)
            
            await conn.execute(
                "INSERT INTO licenses (key, mode, expires_at, active) VALUES ($1, $2, $3, $4)",
                key, mode, expires_at, False
            )
            return key
        finally:
            await conn.close()
