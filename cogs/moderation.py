import discord
from discord.ext import commands
from discord import app_commands
from services.gemini_service import gemini_ai
from core.license_manager import LicenseManager
from database.connection import db
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("Klaud.Moderation")

class Moderation(commands.Cog):
    """
    KLAUD-NINJA INDUSTRIAL MODERATION.
    A multi-threaded AI enforcement system strictly gated by licensing.
    """
    def __init__(self, bot):
        self.bot = bot
        self.default_rules = "Zero tolerance for hate speech, severe toxicity, and advertising."

    async def get_log_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        """Retrieves the designated mod-log channel from the database."""
        row = await db.fetchrow("SELECT log_channel_id FROM guild_settings WHERE guild_id = $1", guild.id)
        if row and row['log_channel_id']:
            return guild.get_channel(row['log_channel_id'])
        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Real-time behavioral enforcement.
        No License = No Operation.
        """
        if message.author.bot or not message.guild:
            return

        # 1. THE GATE: Strict License Enforcement
        if not await LicenseManager.has_access(message.guild.id):
            return

        # 2. AUTHORITY CHECK: Staff are immune to the AI filter
        if message.author.guild_permissions.manage_messages:
            return

        # 3. ANALYSIS: Fetch guild-specific rules from DB
        row = await db.fetchrow("SELECT custom_prompt FROM guild_settings WHERE guild_id = $1", message.guild.id)
        rules = row['custom_prompt'] if row else self.default_rules

        # 4. EXECUTION: Scan content
        # We wrap this in a try-block to ensure one AI failure doesn't crash the cog
        try:
            analysis = await gemini_ai.analyze_content(message.content, rules)
            
            if analysis.get('violation'):
                await self.enforce_protocol(message, analysis)
        except Exception as e:
            logger.error(f"Real-time mod failure: {e}")

    async def enforce_protocol(self, message: discord.Message, data: dict):
        """Handles the heavy-lifting of Discord API punishments."""
        reason = data.get('reason', 'Protocol Violation')
        severity = data.get('severity', 'low')
        suggested_action = data.get('action', 'warn')

        # Action: Deletion
        try: await message.delete()
        except: pass

        # Action: Discord Timeout (If severity is medium/high)
        if severity in ['medium', 'high']:
            duration = timedelta(hours=1) if severity == 'medium' else timedelta(days=1)
            try:
                await message.author.timeout(duration, reason=f"KLAUD AI: {reason}")
                punishment_str = f"TIMED OUT ({duration})"
            except:
                punishment_str = "WARNING (Permissions Missing for Timeout)"
        else:
            punishment_str = "WARNING"

        # Database Log
        await db.execute(
            "INSERT INTO infractions (guild_id, user_id, reason, severity) VALUES ($1, $2, $3, $4)",
            message.guild.id, message.author.id, reason, severity
        )

        # Notify Public
        embed = discord.Embed(title="🛡️ KLAUD INTERVENTION", color=discord.Color.red())
        embed.add_field(name="User", value=message.author.mention, inline=True)
        embed.add_field(name="Action", value=f"**{punishment_str}**", inline=True)
        embed.add_field(name="Reason", value=f"```{reason}```", inline=False)
        await message.channel.send(embed=embed, delete_after=15)

        # Notify Staff Logs
        log_channel = await self.get_log_channel(message.guild)
        if log_channel:
            log_embed = discord.Embed(title="🚨 AI Moderation Log", color=discord.Color.dark_red())
            log_embed.add_field(name="Channel", value=message.channel.mention)
            log_embed.add_field(name="Message Content", value=f"||{message.content}||")
            log_embed.set_footer(text=f"User ID: {message.author.id}")
            await log_channel.send(embed=log_embed)

    @app_commands.command(name="mod_logs", description="View infractions for a user")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def mod_logs(self, interaction: discord.Interaction, user: discord.Member):
        """Administrative lookup for user history."""
        if not await LicenseManager.has_access(interaction.guild.id):
            return await interaction.response.send_message("❌ Unlicensed Server.", ephemeral=True)

        rows = await db.fetch("SELECT reason, severity, created_at FROM infractions WHERE user_id = $1 AND guild_id = $2 ORDER BY created_at DESC LIMIT 5", user.id, interaction.guild.id)
        
        if not rows:
            return await interaction.response.send_message(f"✅ {user.display_name} has a clean record.")

        embed = discord.Embed(title=f"Infraction History: {user}", color=discord.Color.blue())
        for r in rows:
            date = r['created_at'].strftime('%Y-%m-%d')
            embed.add_field(name=f"{date} | {r['severity'].upper()}", value=r['reason'], inline=False)
        
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(Moderation(bot))
