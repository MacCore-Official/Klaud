import discord
from discord.ext import commands
from core.license_manager import LicenseManager
from services.gemini_service import GeminiService
from utils.smart_logger import log_action

class AdminAI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ai = GeminiService()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild: return
        if not self.bot.user.mentioned_in(message): return
        
        # Only Admins/Owners
        if not message.author.guild_permissions.administrator and message.author.id != self.bot.owner_id:
            return

        # Requires PAID license
        if not await LicenseManager.require_paid(message.guild.id):
            await message.reply("⚠️ **AI Admin Commands require a Paid License.**")
            return

        clean_instruction = message.content.replace(f'<@{self.bot.user.id}>', '').strip()
        if not clean_instruction: return

        async with message.channel.typing():
            context = f"Guild: {message.guild.name}, Categories: {[c.name for c in message.guild.categories]}"
            data = await self.ai.parse_admin_command(clean_instruction, context)
            
            actions_taken = []
            if data and "actions" in data:
                for action in data["actions"]:
                    try:
                        await self.execute_action(message.guild, action)
                        actions_taken.append(f"{action.get('type')}: {action.get('name')}")
                    except discord.Forbidden:
                        actions_taken.append(f"❌ Failed (No Perms): {action.get('type')}")
                    except Exception as e:
                        actions_taken.append(f"❌ Failed (Error): {e}")

            if actions_taken:
                result_str = "\n".join(actions_taken)
                await message.reply(f"✅ **AI Actions Executed:**\n```\n{result_str}\n```")
                await log_action(message.guild, "🛠️ AI Admin Action", message.author, result_str, discord.Color.blue())
            else:
                await message.reply("❌ Klaud could not understand the requested action or none were needed.")

    async def execute_action(self, guild: discord.Guild, action: dict):
        a_type = action.get("type")
        name = action.get("name")
        
        if a_type == "create_category":
            await guild.create_category(name)
        elif a_type == "create_channel":
            cat_name = action.get("category")
            category = discord.utils.get(guild.categories, name=cat_name) if cat_name else None
            await guild.create_text_channel(name, category=category)
        elif a_type == "delete_channel":
            channel = discord.utils.get(guild.channels, name=name)
            if channel: await channel.delete()
        elif a_type == "create_role":
            await guild.create_role(name=name)
        elif a_type == "lock_channel":
            channel = discord.utils.get(guild.channels, name=name)
            if channel: await channel.set_permissions(guild.default_role, send_messages=False)
        elif a_type == "unlock_channel":
            channel = discord.utils.get(guild.channels, name=name)
            if channel: await channel.set_permissions(guild.default_role, send_messages=True)

async def setup(bot):
    await bot.add_cog(AdminAI(bot))
