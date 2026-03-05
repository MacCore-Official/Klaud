import discord
from discord.ext import commands
import datetime
import logging
from services.gemini_service import gemini_ai
from core.license_manager import LicenseManager
from database.connection import db

logger = logging.getLogger("Klaud.Moderation")

class Moderation(commands.Cog):
    """KLAUD-NINJA AUTOMATED DEFENSE SYSTEM."""
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        # License Enforcement
        if not await LicenseManager.has_access(message.guild.id):
            return

        # Immunity for Admins
        if message.author.guild_permissions.manage_messages:
            return

        # Neural Content Scan
        try:
            analysis = await gemini_ai.analyze_content(message.content, "Be strict about toxicity.")
            if analysis.get('violation'):
                await self.punish(message, analysis)
        except Exception as e:
            logger.error(f"Moderation Cog Error: {e}")

    async def punish(self, message, data):
        """Deletes message and logs infraction."""
        reason = data.get('reason', 'Protocol Violation')
        try:
            await message.delete()
            
            # DB Logging
            await db.execute(
                "INSERT INTO infractions (guild_id, user_id, reason) VALUES ($1, $2, $3)",
                message.guild.id, message.author.id, reason
            )

            embed = discord.Embed(title="🛡️ KLAUD INTERVENTION", description=f"{message.author.mention}, your message was purged.", color=discord.Color.red())
            embed.add_field(name="Reason", value=f"```{reason}```")
            await message.channel.send(embed=embed, delete_after=10)
        except Exception as e:
            logger.error(f"Punishment Error: {e}")

async def setup(bot):
    await bot.add_cog(Moderation(bot))
