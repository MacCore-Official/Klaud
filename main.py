import discord
from discord.ext import commands
import os
import logging
import asyncio
from database.connection import db
from core.license_manager import LicenseManager
from services.gemini_service import gemini_ai

# Setup Exhaustive Logging test
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("Klaud.Main")

class KlaudBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(
            command_prefix="!", 
            intents=intents,
            help_command=None
        )
        self.owner_id_val = 1269145029943758899

    async def setup_hook(self):
        logger.info("Initializing KLAUD-NINJA Production Systems...")
        await db.connect()

        # Load Extensions
        if os.path.exists('./cogs'):
            for filename in os.listdir('./cogs'):
                if filename.endswith('.py'):
                    try:
                        await self.load_extension(f'cogs.{filename[:-3]}')
                        logger.info(f"✅ Cog Loaded: {filename}")
                    except Exception as e:
                        logger.error(f"❌ Cog Failed: {filename} -> {e}")

        # Global Sync
        await self.tree.sync()
        logger.info("✅ Slash Command Tree Synchronized.")

    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        # 🤖 AI ADMIN HANDLER (PINGS)
        if self.user.mentioned_in(message):
            logger.info(f"Processing AI Request from {message.author} in {message.guild.id}")
            
            # 1. License Check
            # Only users with 'paid' tier can use AI Admin Actions
            is_authorized = await LicenseManager.require_paid(message.guild.id)
            
            if not is_authorized:
                embed = discord.Embed(
                    title="💎 Premium Access Required",
                    description=(
                        "AI Administrative actions (like mass-creating channels) require a **Paid License**.\n\n"
                        "Please use `/license_status` to check your tier or `/license_redeem` to upgrade."
                    ),
                    color=discord.Color.gold()
                )
                return await message.reply(embed=embed)

            # 2. Extract Prompt
            prompt = message.content.replace(f'<@!{self.user.id}>', '').replace(f'<@{self.user.id}>', '').strip()
            
            if not prompt:
                return await message.reply("👋 KLAUD-NINJA Online. How can I manage your server today?")

            # 3. Process Intent with Gemini
            async with message.channel.typing():
                intent = await gemini_ai.parse_admin_intent(prompt)
                
                if intent.get('action') == 'create_channels':
                    count = min(intent.get('count', 1), 10) # Security Cap
                    name = intent.get('base_name', 'staff-channel')
                    
                    # Verify permissions
                    if not message.guild.me.guild_permissions.manage_channels:
                        return await message.reply("❌ Error: I lack the 'Manage Channels' permission.")

                    for i in range(count):
                        await message.guild.create_text_channel(name=f"{name}-{i+1}")
                    
                    await message.reply(f"✅ **AI Task Executed:** Created {count} channels with base name `{name}`.")
                else:
                    await message.reply("🧠 I'm listening, but that action isn't mapped to my admin protocols yet.")

        await self.process_commands(message)

# Global Lifecycle Management
bot = KlaudBot()

async def start_system():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.critical("DISCORD_TOKEN MISSING")
        return

    async with bot:
        await bot.start(token)

if __name__ == "__main__":
    try:
        asyncio.run(start_system())
    except KeyboardInterrupt:
        logger.info("System Shutdown.")
