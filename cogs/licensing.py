import discord
from discord import app_commands
from discord.ext import commands
from core.license_manager import LicenseManager
from utils.smart_logger import log_action

class Licensing(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="license_generate", description="[OWNER] Generate a license key")
    async def license_generate(self, interaction: discord.Interaction, mode: str, duration_days: int):
        if not LicenseManager.is_owner(interaction.user.id):
            return await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        key = await LicenseManager.generate_license(mode.lower(), duration_days)
        await interaction.response.send_message(f"✅ Generated Key: `{key}`\nMode: {mode}\nDays: {duration_days}", ephemeral=True)

    @app_commands.command(name="license_activate", description="Activate Klaud on this server")
    @app_commands.checks.has_permissions(administrator=True)
    async def license_activate(self, interaction: discord.Interaction, key: str):
        success = await LicenseManager.activate_license(interaction.guild.id, interaction.user.id, key)
        if success:
            await interaction.response.send_message("✅ License activated successfully. Klaud is now online.")
            await log_action(interaction.guild, "License Activated", interaction.user, f"Key: {key[:8]}...", discord.Color.green())
        else:
            await interaction.response.send_message("❌ Invalid, expired, or already used key.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Licensing(bot))
