
import discord
from discord.ext import commands, tasks
import logging
from database.connection import db
from core.license_manager import LicenseManager

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("Klaud")

class KlaudBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="k!", intents=intents, help_command=None)

    async def setup_hook(self):
        await db.connect()
        
        cogs = ["cogs.licensing", "cogs.moderation", "cogs.admin_ai", "cogs.setup_verify"]
        for cog in cogs:
            await self.load_extension(cog)
            
        await self.tree.sync()
        self.expire_licenses_task.start()
        log.info("Klaud is ready and synced.")

    @tasks.loop(hours=1)
    async def expire_licenses_task(self):
        if not db.pool: return
        async with db.pool.acquire() as conn:
            await conn.execute("UPDATE licenses SET mode = 'disabled', active = FALSE WHERE expires_at < NOW() AND active = TRUE")

    async def on_guild_join(self, guild: discord.Guild):
        mode = await LicenseManager.get_server_mode(guild.id)
        if mode == "disabled":
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    await channel.send("🛡️ **Klaud is installed but inactive.**\nAn owner must activate a license: `/license activate <key>`")
                    break
