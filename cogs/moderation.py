import discord
from discord.ext import commands
from discord import app_commands
import logging
import datetime
from typing import Optional, List, Dict, Any, Union
from services.gemini_service import gemini_ai
from core.license_manager import LicenseManager
from database.connection import db

logger = logging.getLogger("Klaud.Moderation")

class Moderation(commands.Cog):
    """
    KLAUD-NINJA MODERATION SUBSYSTEM.
    Implements neural content scanning and persistent infraction tracking.
    """
    def __init__(self, bot):
        self.bot = bot
        self.default_rules = "Professional environment. No hate speech, extreme toxicity, or spam."

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Real-time ingress filtering. 
        Checks for valid licensing before invoking neural scan.
        """
        if message.author.bot or not message.guild:
            return

        # 1. License Verification: HARD GATE
        if not await LicenseManager.has_access(message.guild.id):
            return

        # 2. Staff Immunity Protocol
        if message.author.guild_permissions.manage_messages:
            return

        # 3. Rule Retreival
        row = await db.fetchrow(
            "SELECT custom_prompt, log_channel_id FROM guild_settings WHERE guild_id = $1", 
            message.guild.id
        )
        rules = row['custom_prompt'] if row and row['custom_prompt'] else self.default_rules
        log_cid = row['log_channel_id'] if row else None

        # 4. Neural Content Evaluation
        try:
            analysis = await gemini_ai.analyze_content(message.content, rules)
            
            if analysis.get('violation'):
                await self.execute_disciplinary_action(message, analysis, log_cid)
        except Exception as e:
            logger.error(f"MOD: Neural scan crash in {message.guild.id}: {e}")

    async def execute_disciplinary_action(self, message, data, log_cid):
        """Processes the enforcement sequence: Delete -> Log -> Warn."""
        reason = data.get('reason', 'Violation of server protocol.')
        severity = data.get('severity', 'low').lower()

        try:
            # A. Atomic Removal
            await message.delete()

            # B. Database Infraction Recording
            await db.execute(
                """INSERT INTO infractions (guild_id, user_id, reason, severity, timestamp) 
                   VALUES ($1, $2, $3, $4, $5)""",
                message.guild.id, message.author.id, reason, severity, datetime.datetime.now()
            )

            # C. Public Warning
            embed = discord.Embed(
                title="🛡️ KLAUD-NINJA ENFORCEMENT",
                description=f"{message.author.mention}, your message was purged.",
                color=discord.Color.red() if severity == 'high' else discord.Color.orange(),
                timestamp=datetime.datetime.now()
            )
            embed.add_field(name="Violation", value=f"```{reason}```")
            embed.set_footer(text="Enterprise Automated Defense")
            
            warn_msg = await message.channel.send(embed=embed)
            await warn_msg.delete(delay=15)

            # D. Audit Logging
            if log_cid:
                log_chan = message.guild.get_channel(log_cid)
                if log_chan:
                    log_embed = discord.Embed(title="🚨 Moderation Log", color=discord.Color.dark_red())
                    log_embed.add_field(name="User", value=f"{message.author} ({message.author.id})")
                    log_embed.add_field(name="Action", value="Message Purged")
                    log_embed.add_field(name="Content", value=f"||{message.content[:500]}||", inline=False)
                    await log_chan.send(embed=log_embed)

        except discord.Forbidden:
            logger.error(f"MOD: Missing 'Manage Messages' permission in {message.guild.id}")
        except Exception as e:
            logger.error(f"MOD: Action Execution Error: {e}")

    @app_commands.command(name="mod_rules", description="Configure the AI's moderation instructions.")
    @app_commands.describe(instructions="The specific rules KLAUD should enforce.")
    async def mod_rules(self, interaction: discord.Interaction, instructions: str):
        """Allows Administrators to fine-tune the Gemini Filter."""
        if not await LicenseManager.has_access(interaction.guild.id):
            return await interaction.response.send_message("❌ License Required.", ephemeral=True)
        
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Administrative Authority Required.", ephemeral=True)

        await db.execute(
            """INSERT INTO guild_settings (guild_id, custom_prompt) 
               VALUES ($1, $2) ON CONFLICT (guild_id) DO UPDATE SET custom_prompt = $2""",
            interaction.guild.id, instructions
        )
        
        await interaction.response.send_message(f"✅ AI Protocols updated. KLAUD will now enforce: {instructions}")

async def setup(bot):
    await bot.add_cog(Moderation(bot))
