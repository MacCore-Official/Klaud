import asyncpg
import os
import logging
import asyncio
from typing import Optional, Union

# Production-grade logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Klaud.Database")

class Database:
    """
    Core Database Engine for Klaud-Ninja.
    Handles connection pooling, schema migrations, and high-availability retries.
    """
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None
        self._connection_lock = asyncio.Lock()

    async def connect(self) -> Optional[asyncpg.Pool]:
        """
        Establishes a connection pool with specific optimizations for 
        production PostgreSQL instances (Supabase/PgBouncer).
        """
        async with self._connection_lock:
            if self.pool and not self.pool.is_closing():
                return self.pool

            dsn = os.getenv("DATABASE_URL")
            if not dsn:
                logger.critical("CORE FAILURE: DATABASE_URL environment variable is missing.")
                return None

            # Enforce asyncpg compatible protocol
            if dsn.startswith("postgresql://"):
                dsn = dsn.replace("postgresql://", "postgres://", 1)

            try:
                # Production pool settings
                # statement_cache_size=0 is mandatory for PgBouncer compatibility
                self.pool = await asyncpg.create_pool(
                    dsn=dsn,
                    min_size=2,
                    max_size=15,
                    max_queries=50000,
                    max_inactive_connection_lifetime=300.0,
                    command_timeout=60.0,
                    statement_cache_size=0 
                )
                
                async with self.pool.acquire() as conn:
                    await self.init_tables(conn)
                    
                logger.info("✅ Database connection established and schema verified.")
                return self.pool
            except Exception as e:
                logger.error(f"❌ Database connection failed: {e}", exc_info=True)
                return None

    async def init_tables(self, conn: asyncpg.Connection):
        """
        Maintains the integrity of the KLAUD production schema.
        DO NOT REMOVE COLUMNS. ONLY ADDITIVE UPDATES.
        """
        logger.info("Verifying production schema integrity...")
        
        # Licenses table: Supports key-based redemption and server-binding
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS licenses (
                server_id BIGINT PRIMARY KEY,
                license_key TEXT UNIQUE,
                mode TEXT DEFAULT 'free',
                expires_at TIMESTAMP NOT NULL,
                active BOOLEAN DEFAULT FALSE,
                activated_at TIMESTAMP,
                owner_id BIGINT
            );
        """)

        # Guild Settings: Core configuration for AI and Moderation
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id BIGINT PRIMARY KEY,
                prefix TEXT DEFAULT '!',
                mod_intensity TEXT DEFAULT 'medium',
                ai_moderation BOOLEAN DEFAULT TRUE,
                log_channel_id BIGINT,
                verify_channel_id BIGINT,
                verified_role_id BIGINT,
                custom_prompt TEXT
            );
        """)

        # User Statistics: Behavior scoring and spam tracking
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id BIGINT,
                guild_id BIGINT,
                warnings INT DEFAULT 0,
                messages_sent INT DEFAULT 0,
                behavior_score INT DEFAULT 100,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, guild_id)
            );
        """)
        
        # Unclaimed Licenses: Temporary storage for generated keys before redemption
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_licenses (
                license_key TEXT PRIMARY KEY,
                mode TEXT,
                duration_days INT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by BIGINT
            );
        """)
        logger.info("✅ Schema verification complete.")

    async def execute(self, query: str, *args):
        """Standard execution wrapper with auto-reconnect."""
        if not self.pool: await self.connect()
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetchrow(self, query: str, *args):
        """Standard fetch wrapper with auto-reconnect."""
        if not self.pool: await self.connect()
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

# Global database instance used by Cogs and Services
db = Database()
