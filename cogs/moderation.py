import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, List, Dict, Any # MUST HAVE THESE
from services.gemini_service import gemini_ai
from core.license_manager import LicenseManager
from database.connection import db
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("Klaud.Moderation")

class Moderation(commands.Cog):
    """
    KLAUD MODERATION PROTOCOL.
    The primary defensive system for licensed servers.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        # 1. LICENSE GATE (Zero Free Tier)
        if not await LicenseManager.has_access(message.guild.id):
            return

        # 2. STAFF BYPASS
        if message.author.guild_permissions.manage_messages:
            return

        # 3. AI ANALYSIS
        rules = "Strictly moderate swearing, toxicity, and hate speech."
        
        try:
            # We wrap AI calls in a typing indicator to show it's "thinking"
            async with message.channel.typing():
                result = await gemini_ai.analyze_content(message.content, rules)
            
            if result.get('violation'):
                await self.punish_violation(message, result)
        except Exception as e:
            logger.error(f"Mod Failure: {e}")

    async def punish_violation(self, message: discord.Message, data: Dict[str, Any]):
        reason = data.get('reason', 'Violation of server rules.')
        severity = data.get('severity', 'low')

        # Deletion
        try:
            await message.delete()
        except:
            pass

        # Visual Feedback
        embed = discord.Embed(
            title="🛡️ KLAUD INTERVENTION",
            description=f"{message.author.mention}, your content was removed.",
            color=discord.Color.red()
        )
        embed.add_field(name="Reason", value=f"```{reason}```")
        await message.channel.send(embed=embed, delete_after=10)

        # DB Log
        await db.execute(
            "INSERT INTO infractions (guild_id, user_id, reason) VALUES ($1, $2, $3)",
            message.guild.id, message.author.id, reason
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
