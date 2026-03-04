
import asyncpg
import os
import logging

# Set up logging so you can see status in Northflank
logger = logging.getLogger("DB")

class Database:
    def __init__(self):
        self.pool = None

    async def connect(self):
        """Establish connection pool with Supabase-specific fixes."""
        if self.pool:
            return self.pool

        dsn = os.getenv("DATABASE_URL")
        if not dsn:
            logger.error("DATABASE_URL not found in Northflank environment variables!")
            return None

        try:
            # statement_cache_size=0 is MANDATORY for Supabase/PgBouncer
            # This prevents the "DuplicatePreparedStatementError"
            self.pool = await asyncpg.create_pool(
                dsn=dsn,
                min_size=1,
                max_size=10,
                command_timeout=60,
                statement_cache_size=0 
            )
            
            # This part creates the tables in Supabase automatically if they are missing
            async with self.pool.acquire() as conn:
                await self.init_tables(conn)
                
            logger.info("✅ Database connected and tables initialized.")
            return self.pool
        except Exception as e:
            logger.error(f"❌ Database connection failed: {e}")
            return None

    async def init_tables(self, conn):
        """Create the necessary schema if it doesn't exist."""
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS licenses (
                server_id BIGINT PRIMARY KEY,
                key TEXT UNIQUE,
                mode TEXT DEFAULT 'free',
                expires_at TIMESTAMP,
                active BOOLEAN DEFAULT FALSE
            );

            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id BIGINT PRIMARY KEY,
                prefix TEXT DEFAULT '!',
                mod_intensity TEXT DEFAULT 'medium',
                ai_moderation BOOLEAN DEFAULT TRUE
            );

            CREATE TABLE IF NOT EXISTS user_stats (
                user_id BIGINT,
                guild_id BIGINT,
                warnings INT DEFAULT 0,
                messages_sent INT DEFAULT 0,
                PRIMARY KEY (user_id, guild_id)
            );
        """)

# --- CRITICAL LINE BELOW ---
# This allows 'from database.connection import db' to work in your cogs
db = Database()
