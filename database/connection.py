import asyncpg
import os
import logging

logger = logging.getLogger("DB")

class Database:
    def __init__(self):
        self.pool = None

    async def connect(self):
        if self.pool:
            return self.pool
        dsn = os.getenv("DATABASE_URL")
        try:
            # statement_cache_size=0 is the fix for Supabase
            self.pool = await asyncpg.create_pool(
                dsn=dsn,
                min_size=1,
                max_size=10,
                statement_cache_size=0 
            )
            async with self.pool.acquire() as conn:
                await self.init_tables(conn)
            logger.info("✅ Database connected.")
            return self.pool
        except Exception as e:
            logger.error(f"❌ DB Connection Error: {e}")
            return None

    async def init_tables(self, conn):
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS licenses (
                server_id BIGINT PRIMARY KEY,
                key TEXT UNIQUE,
                mode TEXT DEFAULT 'free',
                expires_at TIMESTAMP,
                active BOOLEAN DEFAULT FALSE
            );
        """)

# CRITICAL: This instance must exist for other files to import
db = Database()
