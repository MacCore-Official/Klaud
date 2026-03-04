import discord
from discord import app_commands
from discord.ext import commands
from core.license_manager import LicenseManager

class VerifyButton(discord.ui.View):
    def __init__(self, role: discord.Role):
        super().__init__(timeout=None)
        self.role = role

    @discord.ui.button(label="Verify Here", style=discord.ButtonStyle.success, custom_id="verify_btn")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.user.add_roles(self.role)
        await interaction.response.send_message("✅ You have been verified!", ephemeral=True)

class SetupVerify(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="setup_server", description="Auto-configure the server with AI layouts")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_server(self, interaction: discord.Interaction):
        if not await LicenseManager.require_paid(interaction.guild.id):
            return await interaction.response.send_message("⚠️ `/setup server` requires a Paid License.", ephemeral=True)

        await interaction.response.send_message("⚙️ Initializing Klaud Server Setup...")
        guild = interaction.guild

        # Create Roles
        roles_to_create = ["Moderator", "Verified", "Member", "Muted"]
        created_roles = {}
        for r in roles_to_create:
            role = discord.utils.get(guild.roles, name=r)
            if not role:
                role = await guild.create_role(name=r)
            created_roles[r] = role

        # Muted Role Perms
        for channel in guild.channels:
            await channel.set_permissions(created_roles["Muted"], send_messages=False)

        # Create Categories & Channels
        cat = await guild.create_category("KLAUD SYSTEM")
        rules = await guild.create_text_channel("rules", category=cat)
        verify = await guild.create_text_channel("verification", category=cat)
        announcements = await guild.create_text_channel("announcements", category=cat)
        logs = await guild.create_text_channel("logs", category=cat)
        
        # Setup Verification Message
        embed = discord.Embed(title="Server Verification", description="Click the button below to gain access.", color=discord.Color.blue())
        await verify.send(embed=embed, view=VerifyButton(created_roles["Verified"]))

        await interaction.edit_original_response(content="✅ **Server Setup Complete!**")

async def setup(bot):
    await bot.add_cog(SetupVerify(bot))
