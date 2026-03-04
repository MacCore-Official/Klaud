import asyncpg
import logging
from config import settings

log = logging.getLogger("DB")

class Database:
    def __init__(self):
        self.pool = None

    async def connect(self):
        try:
            self.pool = await asyncpg.create_pool(settings.DATABASE_URL)
            await self.init_tables()
            log.info("Database connected and tables initialized.")
        except Exception as e:
            log.error(f"Database connection failed: {e}")

    async def init_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS licenses (
                    id SERIAL PRIMARY KEY,
                    license_key TEXT UNIQUE,
                    mode TEXT DEFAULT 'disabled',
                    server_id BIGINT UNIQUE,
                    owner_id BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    active BOOLEAN DEFAULT TRUE
                );
                
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id BIGINT PRIMARY KEY,
                    intensity TEXT DEFAULT 'normal',
                    custom_prompt TEXT DEFAULT '',
                    log_channel_id BIGINT DEFAULT 0,
                    ai_mod_enabled BOOLEAN DEFAULT TRUE
                );
                
                CREATE TABLE IF NOT EXISTS user_stats (
                    guild_id BIGINT,
                    user_id BIGINT,
                    toxicity_score INTEGER DEFAULT 0,
                    warnings INTEGER DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                );
            """)

db = Database()
