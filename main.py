import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, List, Dict, Any
from services.gemini_service import gemini_ai
from core.license_manager import LicenseManager
from database.connection import db
import logging
import datetime

logger = logging.getLogger("Klaud.Moderation")

class Moderation(commands.Cog):
    """
    KLAUD AI-DRIVEN MODERATION SUBSYSTEM.
    Implements deep-content analysis and automated disciplinary actions.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.default_rules = "Standard professional moderation. Block toxicity and hate speech."

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Real-time listener for content analysis.
        Strictly gated by LicenseManager.
        """
        if message.author.bot or not message.guild:
            return

        # 1. License Check
        if not await LicenseManager.has_access(message.guild.id):
            return

        # 2. Staff Immunity: Skip AI check for admins/mods
        if message.author.guild_permissions.manage_messages:
            return

        # 3. Rule Context: Fetch server-specific AI instructions
        row = await db.fetchrow(
            "SELECT custom_prompt FROM guild_settings WHERE guild_id = $1", 
            message.guild.id
        )
        current_rules = row['custom_prompt'] if row and row['custom_prompt'] else self.default_rules

        # 4. Neural Analysis
        try:
            analysis = await gemini_ai.analyze_content(message.content, current_rules)
            
            if analysis.get('violation'):
                await self.apply_discipline(message, analysis)
        except Exception as e:
            logger.error(f"Moderation neural failure in {message.guild.id}: {e}")

    async def apply_discipline(self, message: discord.Message, data: Dict[str, Any]):
        """
        Executes the AI-suggested punishment.
        """
        reason = data.get('reason', 'Violation of server protocol.')
        severity = data.get('severity', 'low')
        
        # Action 1: Content Removal
        try:
            await message.delete()
        except discord.Forbidden:
            logger.warning(f"Permissions missing to delete message in {message.guild.id}")

        # Action 2: Infraction Logging
        await db.execute(
            """INSERT INTO infractions (guild_id, user_id, reason, severity, timestamp) 
               VALUES ($1, $2, $3, $4, $5)""",
            message.guild.id, message.author.id, reason, severity, datetime.datetime.now()
        )

        # Action 3: User Notification
        embed = discord.Embed(
            title="🛡️ KLAUD INTERVENTION",
            description=f"{message.author.mention}, your content was removed for violating server protocols.",
            color=discord.Color.red() if severity == 'high' else discord.Color.orange(),
            timestamp=datetime.datetime.now()
        )
        embed.add_field(name="Reason", value=f"```{reason}```")
        embed.set_footer(text=f"Severity: {severity.upper()} | Automated AI Moderation")
        
        await message.channel.send(embed=embed, delete_after=15)
        logger.info(f"MODERATION: Purged content from {message.author} in {message.guild.id}")

    @app_commands.command(name="mod_config", description="Update the AI moderation instructions for KLAUD.")
    @app_commands.describe(prompt="The rules KLAUD should follow (e.g. 'Be strict about swearing')")
    @app_commands.checks.has_permissions(administrator=True)
    async def mod_config(self, interaction: discord.Interaction, prompt: str):
        """Allows administrators to fine-tune the AI brain."""
        if not await LicenseManager.has_access(interaction.guild.id):
            return await interaction.response.send_message("❌ System License Required.", ephemeral=True)

        await db.execute(
            """INSERT INTO guild_settings (guild_id, custom_prompt) 
               VALUES ($1, $2) ON CONFLICT (guild_id) DO UPDATE SET custom_prompt = $2""",
            interaction.guild.id, prompt
        )
        await interaction.response.send_message(f"✅ **AI Protocols Updated.** KLAUD will now: {prompt}")

async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
