import discord
from discord.ext import commands
from services.gemini_service import gemini_ai
from core.license_manager import LicenseManager
import logging

logger = logging.getLogger("Klaud.AdminAI")

class AdminAI(commands.Cog):
    """
    Handles natural language administrative tasks via Gemini.
    """
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        # Trigger only on bot mention
        if self.bot.user.mentioned_in(message):
            # STRICT LICENSE GATE
            has_paid = await LicenseManager.require_paid(message.guild.id)
            if not has_paid:
                return # Silence or a minimal "Unlicensed" reply in main.py covers this.

            prompt = message.content.replace(f'<@!{self.bot.user.id}>', '').replace(f'<@{self.bot.user.id}>', '').strip()
            if not prompt:
                return

            async with message.channel.typing():
                # FIXED: Method name aligned with GeminiService
                intent = await gemini_ai.parse_admin_intent(prompt)
                
                if intent.get('action') == 'create_channels':
                    count = min(intent.get('count', 1), 5) 
                    name = intent.get('base_name', 'staff-channel')
                    
                    if not message.guild.me.guild_permissions.manage_channels:
                        return await message.reply("❌ I don't have 'Manage Channels' permissions.")

                    for i in range(count):
                        await message.guild.create_text_channel(name=f"{name}-{i+1}")
                    
                    await message.reply(f"✅ Created {count} channels as requested.")
                
                elif intent.get('action') == 'none':
                    # Fallback for general conversation if paid
                    pass 

async def setup(bot):
    await bot.add_cog(AdminAI(bot))
