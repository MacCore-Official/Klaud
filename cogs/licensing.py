import discord
from discord import app_commands
from discord.ext import commands
from core.license_manager import LicenseManager

class Licensing(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="license_generate", description="Generate a new license key")
    async def license_generate(self, interaction: discord.Interaction, mode: str, duration_days: int):
        # This 'defer' stops the 'Application did not respond' error 
        # by giving the bot 15 minutes to think instead of 3 seconds.
        await interaction.response.defer(ephemeral=True)
        
        try:
            key = await LicenseManager.generate_license(mode, duration_days)
            await interaction.followup.send(f"✅ Generated {mode} license: `{key}`")
        except Exception as e:
            print(f"Error in license_generate: {e}")
            await interaction.followup.send(f"❌ Database error. Check Northflank logs.")

async def setup(bot):
    await bot.add_cog(Licensing(bot))
