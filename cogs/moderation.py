import discord
from discord.ext import commands
from discord import app_commands
from services.gemini_service import gemini_ai
from core.license_manager import LicenseManager
from database.connection import db
import logging
from datetime import datetime

logger = logging.getLogger("Klaud.Moderation")

class Moderation(commands.Cog):
    """
    KLAUD-NINJA COMMANDER MODERATION.
    High-fidelity AI behavioral analysis. 
    NO LICENSE = NO MODERATION.
    """
    def __init__(self, bot):
        self.bot = bot
        self.system_id = 1269145029943758899

    async def get_config(self, guild_id: int):
        """Fetches the specific AI moderation context for this guild."""
        row = await db.fetchrow("SELECT mod_intensity, custom_prompt FROM guild_settings WHERE guild_id = $1", guild_id)
        if not row:
            return {"intensity": "medium", "rules": "Strictly moderate swearing, hate speech, and toxicity."}
        return {"intensity": row['mod_intensity'], "rules": row['custom_prompt']}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        The Gatekeeper Listener.
        Evaluates every message against the KLAUD licensing engine.
        """
        if message.author.bot or not message.guild:
            return

        # 1. License Enforcement
        # If the server is not activated, KLAUD ignores the message entirely.
        if not await LicenseManager.has_access(message.guild.id):
            return

        # 2. Staff Bypass
        if message.author.guild_permissions.manage_messages or message.author.id == self.system_id:
            return

        # 3. Contextual AI Analysis
        # We fetch specific rules to ensure "swearing" is handled according to the owner's prompt.
        config = await self.get_config(message.guild.id)
        
        async with message.channel.typing():
            analysis = await gemini_ai.analyze_content(
                content=message.content, 
                rules=config['rules']
            )

        # 4. Action Execution
        if analysis.get('violation'):
            await self.apply_enforcement(message, analysis)

    async def apply_enforcement(self, message: discord.Message, analysis: dict):
        """
        Executes the disciplinary protocol determined by the AI.
        Includes deletion, behavioral logging, and public warning.
        """
        reason = analysis.get('reason', 'Violation of server protocols.')
        severity = analysis.get('severity', 'low')

        try:
            # Atomic Action: Delete first to stop spread
            await message.delete()

            # Database Tracking: Permanent behavioral scoring
            await db.execute("""
                INSERT INTO user_stats (user_id, guild_id, warnings, behavior_score)
                VALUES ($1, $2, 1, 90)
                ON CONFLICT (user_id, guild_id) DO UPDATE SET 
                warnings = user_stats.warnings + 1,
                behavior_score = user_stats.behavior_score - 10
            """, message.author.id, message.guild.id)

            # Visual Enforcement
            embed = discord.Embed(
                title="🛡️ KLAUD-NINJA INTERVENTION",
                description=f"{message.author.mention}, your message was purged.",
                color=discord.Color.red() if severity == 'high' else discord.Color.orange(),
                timestamp=datetime.now()
            )
            embed.add_field(name="Protocol Violation", value=f"```{reason}```")
            embed.set_footer(text="License Tier: ENTERPRISE | Automated Moderation Active")
            
            await message.channel.send(embed=embed, delete_after=15)
            logger.info(f"Purged content in {message.guild.id} from {message.author.id}. Reason: {reason}")

        except discord.Forbidden:
            logger.error(f"CRITICAL: KLAUD lacks Manage Messages permission in {message.guild.id}")
        except Exception as e:
            logger.error(f"Moderation Execution Error: {e}", exc_info=True)

    @app_commands.command(name="mod_rules", description="Update the AI's moderation instructions")
    @app_commands.describe(prompt="The rules KLAUD should follow (e.g., 'Warn for any swearing')")
    async def mod_rules(self, interaction: discord.Interaction, prompt: str):
        """Allows licensed owners to fine-tune the Gemini Shield."""
        if not await LicenseManager.has_access(interaction.guild.id):
            return await interaction.response.send_message("❌ Server not licensed.", ephemeral=True)

        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Administrative Authority Required.", ephemeral=True)

        await db.execute("""
            INSERT INTO guild_settings (guild_id, custom_prompt)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE SET custom_prompt = $2
        """, interaction.guild.id, prompt)

        await interaction.response.send_message(f"✅ **AI Protocols Updated.** KLAUD will now: {prompt}")

async def setup(bot):
    await bot.add_cog(Moderation(bot))
