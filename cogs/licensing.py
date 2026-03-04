import discord
from discord import app_commands
from discord.ext import commands
from core.license_manager import LicenseManager

class Licensing(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="license_generate", description="Create a license key")
    async def license_generate(self, interaction: discord.Interaction, mode: str, duration_days: int):
        # This MUST be first. It buys the bot time so it doesn't "not respond"
        await interaction.response.defer(ephemeral=True)
        
        try:
            key = await LicenseManager.generate_license(mode, duration_days)
            await interaction.followup.send(f"✅ **Key Generated:** `{key}`")
        except Exception as e:
            await interaction.followup.send(f"❌ Database error: {e}")

async def setup(bot):
    await bot.add_cog(Licensing(bot))
