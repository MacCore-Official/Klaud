import discord
from discord.ext import commands
import os
import asyncio
import logging
import traceback
from services.gemini_service import gemini_ai
from core.license_manager import LicenseManager

logger = logging.getLogger("Klaud.Kernel")

class KlaudNinja(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all())

    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild: return
        
        # Check if the bot was pinged
        if self.user.mentioned_in(message) and not message.mention_everyone:
            if not await LicenseManager.has_access(message.guild.id):
                return await message.reply("🔒 **License Required.**")
            
            await self.handle_ai_command(message)

    async def handle_ai_command(self, message: discord.Message):
        """The Brain-to-Action Pipeline."""
        async with message.channel.typing():
            # Get the intent from Gemini
            intent = await gemini_ai.parse_admin_intent(message.content)
            action = intent.get('action', 'none')
            
            logger.info(f"ACTION DETECTED: {action} for query: {message.content}")

            # 1. CREATE CHANNELS
            if action == 'create_channels':
                count = intent.get('count', 1)
                # Default to 'test' if AI missed the name
                name = intent.get('base_name') or "test-channel"
                
                await message.reply(f"🛠️ **Protocol:** Creating {count} channel(s) named `{name}`...")
                for i in range(count):
                    suffix = f"-{i+1}" if count > 1 else ""
                    await message.guild.create_text_channel(f"{name}{suffix}")
                await message.reply("✅ **Task Complete.**")

            # 2. DELETE CHANNELS
            elif action == 'delete_channels':
                await message.reply("☢️ **CRITICAL:** Deleting channels in 5s. (Except this one).")
                await asyncio.sleep(5)
                for channel in message.guild.channels:
                    if channel.id != message.channel.id: # Save current channel to see result
                        try: await channel.delete()
                        except: continue
                await message.channel.send("✅ **Purge Protocol Complete.**")

            # 3. SETUP ROBLOX SERVER
            elif action == 'setup_server':
                await message.reply("🏗️ **Sequence:** Generating Roblox Trading Template...")
                # Category 1: Info
                cat_info = await message.guild.create_category("━━━ INFORMATION ━━━")
                await cat_info.create_text_channel("rules")
                await cat_info.create_text_channel("announcements")
                # Category 2: Trading
                cat_trade = await message.guild.create_category("━━━ TRADING ━━━")
                await cat_trade.create_text_channel("trading-floor")
                await cat_trade.create_text_channel("vouch-proofs")
                await cat_trade.create_text_channel("middleman-call")
                
                await message.reply("✅ **Setup Complete.** Your Roblox Trading hub is ready.")

            # 4. FALLBACK
            else:
                # We show the user what we 'tried' to find to help debug
                await message.reply(f"🧠 **KLAUD AI:** I heard: `{message.content}`\nBut I couldn't map that to a system command. Try: 'Make 3 channels named test'")

# Entry point logic...
if __name__ == "__main__":
    bot = KlaudNinja()
    # ... (Start code from previous version)
