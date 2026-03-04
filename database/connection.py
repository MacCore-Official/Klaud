import asyncpg
import os
import logging
import asyncio
from typing import Optional, List

# Production-grade logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Klaud.Database")

class Database:
    """
    KLAUD-NINJA Persistence Layer.
    Includes an automated migration engine to prevent 'UndefinedColumnError'.
    """
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> Optional[asyncpg.Pool]:
        async with self._lock:
            if self.pool:
                return self.pool

            # Convert URL for asyncpg compatibility
            dsn = os.getenv("DATABASE_URL", "").replace("postgresql://", "postgres://", 1)
            if not dsn:
                logger.critical("DATABASE_URL is missing! System cannot persist data.")
                return None

            try:
                # Production pool configuration for PgBouncer/Supabase
                self.pool = await asyncpg.create_pool(
                    dsn=dsn,
                    min_size=5,
                    max_size=20,
                    statement_cache_size=0, # Mandatory for PgBouncer
                    command_timeout=60.0
                )
                
                async with self.pool.acquire() as conn:
                    await self._run_migrations(conn)
                
                logger.info("✅ Database connected and schema migrations verified.")
                return self.pool
            except Exception as e:
                logger.error(f"❌ Database connection failed: {e}", exc_info=True)
                return None

    async def _run_migrations(self, conn: asyncpg.Connection):
        """
        Self-healing schema logic. 
        Automatically detects missing columns and adds them.
        """
        logger.info("Running KLAUD-NINJA Schema Integrity Check...")

        # 1. Base Licenses Table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS licenses (
                server_id BIGINT PRIMARY KEY,
                license_key TEXT UNIQUE,
                mode TEXT DEFAULT 'free',
                expires_at TIMESTAMP NOT NULL,
                active BOOLEAN DEFAULT FALSE
            );
        """)

        # 2. Migration: Add missing columns to existing 'licenses' table
        required_columns = [
            ("activated_at", "TIMESTAMP"),
            ("owner_id", "BIGINT")
        ]

        for col_name, col_type in required_columns:
            # SQL to add column if it doesn't exist
            await conn.execute(f"""
                DO $$ 
                BEGIN 
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='licenses' AND column_name='{col_name}') THEN 
                        ALTER TABLE licenses ADD COLUMN {col_name} {col_type}; 
                        RAISE NOTICE 'Added missing column: {col_name}';
                    END IF; 
                END $$;
            """)

        # 3. Pending Licenses (Keys waiting for redemption)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_licenses (
                license_key TEXT PRIMARY KEY,
                mode TEXT,
                duration_days INT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by BIGINT
            );
        """)
        
        logger.info("✅ Schema integrity check complete.")

    async def fetchrow(self, query: str, *args):
        if not self.pool: await self.connect()
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def execute(self, query: str, *args):
        if not self.pool: await self.connect()
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

# Global DB instance
db = Database()
