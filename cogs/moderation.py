import discord
from discord.ext import commands
from discord import app_commands
import logging
import datetime
import asyncio
from typing import Optional, List, Dict, Any, Union
from services.gemini_service import gemini_ai
from core.license_manager import LicenseManager
from database.connection import db

logger = logging.getLogger("Klaud.Moderation")

class Moderation(commands.Cog):
    """
    KLAUD-NINJA MODERATION MODULE.
    Integrated neural scanning and infraction persistence.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.default_prompt = "Maintain a professional, clean environment. No toxicity."

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Real-time behavioral monitoring."""
        if message.author.bot or not message.guild:
            return

        # 1. LICENSE ENFORCEMENT
        if not await LicenseManager.has_access(message.guild.id):
            return

        # 2. STAFF IMMUNITY
        if message.author.guild_permissions.manage_messages:
            return

        # 3. RULE RETRIEVAL
        # Fetches custom server-defined AI instructions from the database
        row = await db.fetchrow(
            "SELECT custom_prompt, log_channel_id FROM guild_settings WHERE guild_id = $1", 
            message.guild.id
        )
        rules = row['custom_prompt'] if row and row['custom_prompt'] else self.default_prompt
        log_channel_id = row['log_channel_id'] if row else None

        # 4. NEURAL CONTENT ANALYSIS
        try:
            # Visual feedback that AI is processing
            async with message.channel.typing():
                analysis = await gemini_ai.analyze_content(message.content, rules)
            
            if analysis.get('violation'):
                await self.execute_punishment(message, analysis, log_channel_id)
        except Exception as e:
            logger.error(f"MOD: Analysis error in {message.guild.id}: {e}")

    async def execute_punishment(self, message: discord.Message, data: Dict[str, Any], log_id: Optional[int]):
        """Executes the disciplinary action sequence."""
        reason = data.get('reason', 'Violation of server protocol.')
        severity = data.get('severity', 'low').lower()

        try:
            # A. Atomic Deletion
            await message.delete()

            # B. Database Logging
            await db.execute(
                """INSERT INTO infractions (guild_id, user_id, reason, severity, timestamp) 
                   VALUES ($1, $2, $3, $4, $5)""",
                message.guild.id, message.author.id, reason, severity, datetime.datetime.now()
            )

            # C. Warning Embed
            embed = discord.Embed(
                title="🛡️ KLAUD: CONTENT REMOVED",
                description=f"{message.author.mention}, your content was purged for violating protocols.",
                color=discord.Color.red() if severity == 'high' else discord.Color.orange(),
                timestamp=datetime.datetime.now()
            )
            embed.add_field(name="Protocol Reason", value=f"```{reason}```")
            embed.set_footer(text="Enterprise Automated Defense")
            
            alert = await message.channel.send(embed=embed)
            await alert.delete(delay=20)

            # D. Audit Log Transmission
            if log_id:
                log_chan = self.bot.get_channel(log_id)
                if log_chan:
                    log_embed = discord.Embed(title="🚨 AI Moderation Log", color=discord.Color.dark_red())
                    log_embed.add_field(name="Target", value=f"{message.author} ({message.author.id})")
                    log_embed.add_field(name="Severity", value=severity.upper())
                    log_embed.add_field(name="Content Snippet", value=f"||{message.content[:400]}||", inline=False)
                    await log_chan.send(embed=log_embed)

        except discord.Forbidden:
            logger.error(f"MOD: Permission denied in {message.guild.id}.")
        except Exception as e:
            logger.error(f"MOD: Punishment failed: {e}")

    @app_commands.command(name="config_ai", description="Update the AI instructions for this server.")
    @app_commands.describe(instructions="The specific rules KLAUD should enforce (e.g. 'No swearing')")
    @app_commands.checks.has_permissions(administrator=True)
    async def config_ai(self, interaction: discord.Interaction, instructions: str):
        """Allows admins to customize the Gemini filter."""
        if not await LicenseManager.has_access(interaction.guild.id):
            return await interaction.response.send_message("❌ License Required.", ephemeral=True)

        await db.execute(
            """INSERT INTO guild_settings (guild_id, custom_prompt) 
               VALUES ($1, $2) ON CONFLICT (guild_id) DO UPDATE SET custom_prompt = $2""",
            interaction.guild.id, instructions
        )
        
        await interaction.response.send_message(f"✅ **AI Protocols Updated.** New logic: {instructions}")

async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
