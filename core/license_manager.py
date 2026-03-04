import secrets
import string
import datetime
from database.connection import db
from config.settings import OWNER_ID

class LicenseManager:
    @staticmethod
    def is_owner(user_id: int) -> bool:
        return user_id == OWNER_ID

    @staticmethod
    async def get_server_mode(guild_id: int) -> str:
        if not db.pool: return "disabled"
        async with db.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT mode, expires_at, active FROM licenses WHERE server_id = $1", guild_id)
            if not row or not row['active']:
                return "disabled"
            if row['expires_at'] and row['expires_at'] < datetime.datetime.now():
                await conn.execute("UPDATE licenses SET mode = 'disabled', active = FALSE WHERE server_id = $1", guild_id)
                return "disabled"
            return row['mode']

    @staticmethod
    async def has_access(guild_id: int) -> bool:
        mode = await LicenseManager.get_server_mode(guild_id)
        return mode in ["free", "paid"]

    @staticmethod
    async def require_paid(guild_id: int) -> bool:
        mode = await LicenseManager.get_server_mode(guild_id)
        return mode == "paid"

    @staticmethod
    def generate_license_key() -> str:
        part1 = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
        part2 = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
        part3 = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
        return f"KLAUD-{part1}-{part2}-{part3}"

    @staticmethod
    async def generate_license(mode: str, duration_days: int) -> str:
        key = LicenseManager.generate_license_key()
        expires = datetime.datetime.now() + datetime.timedelta(days=duration_days) if duration_days > 0 else None
        async with db.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO licenses (license_key, mode, expires_at, active) VALUES ($1, $2, $3, TRUE)",
                key, mode, expires
            )
        return key

    @staticmethod
    async def activate_license(guild_id: int, owner_id: int, key: str) -> bool:
        async with db.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT id, active FROM licenses WHERE license_key = $1 AND server_id IS NULL", key)
            if not row or not row['active']: return False
            
            # Deactivate old license for this server if exists
            await conn.execute("UPDATE licenses SET active = FALSE WHERE server_id = $1", guild_id)
            
            # Bind new license
            await conn.execute(
                "UPDATE licenses SET server_id = $1, owner_id = $2 WHERE id = $3",
                guild_id, owner_id, row['id']
            )
            return True
