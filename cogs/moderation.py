
import discord
from discord import app_commands
from discord.ext import commands
import datetime
from database.connection import db
from core.license_manager import LicenseManager
from services.gemini_service import GeminiService
from utils.smart_logger import log_action

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ai = GeminiService()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild: return
        
        # Access Check
        if not await LicenseManager.has_access(message.guild.id): return

        async with db.pool.acquire() as conn:
            cfg = await conn.fetchrow("SELECT * FROM guild_settings WHERE guild_id = $1", message.guild.id)
            if not cfg:
                await conn.execute("INSERT INTO guild_settings (guild_id) VALUES ($1)", message.guild.id)
                cfg = {'intensity': 'normal', 'custom_prompt': '', 'ai_mod_enabled': True}

        if not cfg['ai_mod_enabled'] or message.author.guild_permissions.administrator: return

        # AI Moderation Execution
        analysis = await self.ai.analyze_message(message.content, cfg['intensity'], cfg['custom_prompt'])
        
        if analysis.get('is_violating'):
            await message.delete()
            
            # Increment Warnings
            async with db.pool.acquire() as conn:
                stats = await conn.fetchrow("SELECT warnings FROM user_stats WHERE guild_id = $1 AND user_id = $2", message.guild.id, message.author.id)
                if not stats:
                    await conn.execute("INSERT INTO user_stats (guild_id, user_id, warnings) VALUES ($1, $2, 1)", message.guild.id, message.author.id)
                    warnings = 1
                else:
                    warnings = stats['warnings'] + 1
                    await conn.execute("UPDATE user_stats SET warnings = $1 WHERE guild_id = $2 AND user_id = $3", warnings, message.guild.id, message.author.id)

            # Escalating Punishments Logic
            reason = f"AI Mod: {analysis.get('reason')} (Warn #{warnings})"
            try:
                if warnings == 1:
                    await message.channel.send(f"⚠️ {message.author.mention}, {analysis.get('reason')}", delete_after=5)
                elif warnings == 2:
                    await message.author.timeout(datetime.timedelta(minutes=10), reason=reason)
                elif warnings == 3:
                    await message.author.timeout(datetime.timedelta(hours=1), reason=reason)
                elif warnings == 4:
                    await message.author.kick(reason=reason)
                elif warnings >= 5:
                    await message.author.ban(reason=reason)
            except discord.Forbidden:
                pass # Bot lacks permission to punish

            await log_action(message.guild, "🛡️ AI Auto-Moderation", message.author, reason, discord.Color.red())

    @app_commands.command(name="intensity", description="Set AI Moderation strictness")
    @app_commands.choices(level=[
        app_commands.Choice(name="Relaxed", value="relaxed"),
        app_commands.Choice(name="Normal", value="normal"),
        app_commands.Choice(name="Strict", value="strict"),
        app_commands.Choice(name="Extreme", value="extreme"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def set_intensity(self, interaction: discord.Interaction, level: app_commands.Choice[str]):
        if not await LicenseManager.has_access(interaction.guild.id):
            return await interaction.response.send_message("This server is not authorized to use Klaud.", ephemeral=True)
        async with db.pool.acquire() as conn:
            await conn.execute("UPDATE guild_settings SET intensity = $1 WHERE guild_id = $2", level.value, interaction.guild.id)
        await interaction.response.send_message(f"✅ Moderation intensity set to **{level.value}**.")

    @app_commands.command(name="ai-prompt", description="Set custom rules for AI Moderation")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_prompt(self, interaction: discord.Interaction, text: str):
        if not await LicenseManager.has_access(interaction.guild.id):
            return await interaction.response.send_message("This server is not authorized to use Klaud.", ephemeral=True)
        async with db.pool.acquire() as conn:
            await conn.execute("UPDATE guild_settings SET custom_prompt = $1 WHERE guild_id = $2", text, interaction.guild.id)
        await interaction.response.send_message(f"✅ Custom AI Prompt updated:\n> {text}")

async def setup(bot):
    await bot.add_cog(Moderation(bot))
