import discord
from discord import app_commands
from discord.ext import commands
from core.license_manager import LicenseManager
import logging

logger = logging.getLogger("Klaud.Licensing")

class Licensing(commands.Cog):
    """
    KLAUD Licensing & Authority Subsystem.
    Handles the generation of keys by the bot owner and redemption by server administrators.
    """
    def __init__(self, bot):
        self.bot = bot
        self.owner_id = 1269145029943758899  # KLAUD Authority

    @app_commands.command(name="license_generate", description="[OWNER ONLY] Generate a KLAUD license key")
    @app_commands.describe(mode="License tier: 'free' or 'paid'", duration_days="Validity period in days")
    async def license_generate(self, interaction: discord.Interaction, mode: str, duration_days: int):
        """Generates a new, unredeemed license key."""
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("❌ **Authority Denied:** Only the KLAUD Owner can generate licenses.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        if mode.lower() not in ['free', 'paid']:
            return await interaction.followup.send("❌ Invalid mode. Choose 'free' or 'paid'.")

        try:
            key = await LicenseManager.generate_license(mode.lower(), duration_days, interaction.user.id)
            
            embed = discord.Embed(
                title="License Generated Successfully",
                color=discord.Color.gold(),
                description=f"Tier: **{mode.upper()}**\nDuration: **{duration_days} Days**"
            )
            embed.add_field(name="License Key", value=f"```\n{key}\n```")
            embed.set_footer(text="Keep this key secure. It is single-use.")
            
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"License generation error: {e}")
            await interaction.followup.send("❌ System failure during key generation.")

    @app_commands.command(name="license_redeem", description="Activate KLAUD-Ninja for this server")
    @app_commands.describe(key="The KLAUD license key provided by the owner")
    @app_commands.checks.has_permissions(administrator=True)
    async def license_redeem(self, interaction: discord.Interaction, key: str):
        """Redeems an unlinked key and binds it to the current guild."""
        await interaction.response.defer(ephemeral=True)

        result = await LicenseManager.redeem_license(interaction.guild.id, key.strip(), interaction.user.id)

        if result["success"]:
            embed = discord.Embed(
                title="Server Activated",
                color=discord.Color.green(),
                description=f"KLAUD-Ninja is now authorized for **{interaction.guild.name}**."
            )
            embed.add_field(name="Tier", value=result["mode"].upper(), inline=True)
            embed.add_field(name="Expiration", value=result["expiry"].strftime('%Y-%m-%d %H:%M'), inline=True)
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            
            await interaction.followup.send(embed=embed)
            
            # Synchronize commands for the guild specifically after activation
            try:
                await self.bot.tree.sync(guild=interaction.guild)
            except:
                pass
        else:
            await interaction.followup.send(f"❌ **Redemption Failed:** {result['message']}")

    @app_commands.command(name="license_status", description="Check the license status of this server")
    async def license_status(self, interaction: discord.Interaction):
        """Provides transparency on the current server's authorization status."""
        await interaction.response.defer(ephemeral=True)
        
        license_info = await LicenseManager.get_license_info(interaction.guild.id)
        
        if not license_info or not license_info['active']:
            return await interaction.followup.send("⚠️ This server is currently **unlicensed**. Some AI features may be disabled.")

        expiry = license_info['expires_at']
        status = "Active" if expiry > discord.utils.utcnow().replace(tzinfo=None) else "Expired"
        
        embed = discord.Embed(
            title="KLAUD License Information",
            color=discord.Color.blue() if status == "Active" else discord.Color.red()
        )
        embed.add_field(name="Server", value=interaction.guild.name, inline=False)
        embed.add_field(name="Status", value=f"`{status}`", inline=True)
        embed.add_field(name="Tier", value=f"`{license_info['mode'].upper()}`", inline=True)
        embed.add_field(name="Expiration Date", value=expiry.strftime('%Y-%m-%d'), inline=False)
        
        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Licensing(bot))
