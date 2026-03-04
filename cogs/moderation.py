import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, List, Dict
from services.gemini_service import gemini_ai
from core.license_manager import LicenseManager
from database.connection import db
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("Klaud.Moderation")

class Moderation(commands.Cog):
    """
    KLAUD-NINJA MODERATION ENGINE.
    High-fidelity AI behavioral filtering.
    """
    def __init__(self, bot):
        self.bot = bot
        self.default_prompt = "Moderate swearing, toxicity, and hate speech strictly."

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        # 1. Global License Gate (No free tier)
        if not await LicenseManager.has_access(message.guild.id):
            return

        # 2. Staff Immunity
        if message.author.guild_permissions.manage_messages:
            return

        # 3. AI Processing
        # Fetch server-specific rules
        row = await db.fetchrow("SELECT custom_prompt FROM guild_settings WHERE guild_id = $1", message.guild.id)
        rules = row['custom_prompt'] if row else self.default_prompt

        try:
            analysis = await gemini_ai.analyze_content(message.content, rules)
            if analysis.get('violation'):
                await self.execute_punishment(message, analysis)
        except Exception as e:
            logger.error(f"Moderation Logic Error: {e}")

    async def execute_punishment(self, message: discord.Message, data: dict):
        reason = data.get('reason', 'Protocol Violation')
        severity = data.get('severity', 'low')
        
        # Deletion
        try:
            await message.delete()
        except discord.Forbidden:
            logger.warning(f"Missing permissions to delete in {message.guild.id}")

        # Punishment Escalation
        if severity == 'high':
            try:
                await message.author.timeout(timedelta(hours=24), reason=f"KLAUD AI: {reason}")
                action_taken = "24-HOUR TIMEOUT"
            except:
                action_taken = "DELETED (Missing Permissions for Timeout)"
        else:
            action_taken = "MESSAGE DELETED"

        # Database Log
        await db.execute(
            "INSERT INTO infractions (guild_id, user_id, reason, severity) VALUES ($1, $2, $3, $4)",
            message.guild.id, message.author.id, reason, severity
        )

        # UI Response
        embed = discord.Embed(title="🛡️ KLAUD INTERVENTION", color=discord.Color.red())
        embed.add_field(name="User", value=message.author.mention)
        embed.add_field(name="Status", value=action_taken)
        embed.add_field(name="Reason", value=f"```{reason}```")
        await message.channel.send(embed=embed, delete_after=10)

    @app_commands.command(name="mod_rules", description="Define how KLAUD should moderate this server.")
    @app_commands.describe(rules="Example: 'Warn for swearing, but ban for hate speech'")
    @app_commands.checks.has_permissions(administrator=True)
    async def mod_rules(self, interaction: discord.Interaction, rules: str):
        if not await LicenseManager.has_access(interaction.guild.id):
            return await interaction.response.send_message("❌ Server not licensed.", ephemeral=True)

        await db.execute("""
            INSERT INTO guild_settings (guild_id, custom_prompt) 
            VALUES ($1, $2) ON CONFLICT (guild_id) DO UPDATE SET custom_prompt = $2
        """, interaction.guild.id, rules)
        
        await interaction.response.send_message(f"✅ AI Protocols updated. KLAUD will now: {rules}")

async def setup(bot):
    await bot.add_cog(Moderation(bot))
