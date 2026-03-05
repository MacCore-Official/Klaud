import discord
from discord.ext import commands
from discord import app_commands
import os
import sys
import asyncio
import logging
import datetime
import time
import signal
import traceback
import platform
import json
from typing import Optional, List, Union, Dict, Any, Literal

# Internal Infrastructure Authority
from database.connection import db
from core.license_manager import LicenseManager
from services.gemini_service import gemini_ai

# --- INDUSTRIAL LOGGING CONFIGURATION ---
class KlaudSystemFormatter(logging.Formatter):
    """Custom logs designed for Northflank container readability."""
    def format(self, record):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        level = record.levelname
        name = record.name
        message = record.getMessage()
        return f"[{timestamp}] [{level}] [{name}]: {message}"

logger = logging.getLogger("Klaud.Kernel")
logger.setLevel(logging.INFO)
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(KlaudSystemFormatter())
logger.addHandler(stream_handler)

# --- THE MASTER BOT ORCHESTRATOR ---
class KlaudNinja(commands.Bot):
    """
    KLAUD-NINJA ENTERPRISE OS v5.2.0
    A high-availability bot designed for Roblox Trading and Server Management.
    """
    def __init__(self):
        # Intents: Total visibility required for admin commands
        _intents = discord.Intents.all()
        _intents.members = True
        _intents.message_content = True
        
        super().__init__(
            command_prefix="!",
            intents=_intents,
            help_command=None,
            owner_id=1269145029943758899,
            chunk_guilds_at_startup=True,
            heartbeat_timeout=150.0
        )
        
        # System State
        self.start_time = time.time()
        self.version = "2026.STABLE.PRO"
        self.deployment_id = f"KLAUD-CORE-{os.urandom(2).hex().upper()}"
        self.processed_intents = 0

    async def setup_hook(self) -> None:
        """The pre-gateway initialization sequence."""
        logger.info("=" * 60)
        logger.info(f"BOOTING KLAUD-NINJA OS | DEPLOYMENT ID: {self.deployment_id}")
        logger.info(f"OS: {platform.system()} | VERSION: {self.version}")
        logger.info("=" * 60)
        
        # 1. Database Connection Authority
        try:
            await db.connect()
            logger.info("✅ DATABASE: Connection Pool established.")
        except Exception as e:
            logger.critical(f"❌ DATABASE: Failed to connect. Aborting boot. Error: {e}")
            sys.exit(1)

        # 2. Subsystem Mounting (Cogs)
        await self._mount_cogs()

        # 3. Global Command Sync
        try:
            logger.info("🔄 API: Synchronizing Application Command Tree...")
            synced = await self.tree.sync()
            logger.info(f"✅ API: {len(synced)} Global Commands Synchronized.")
        except Exception as e:
            logger.error(f"⚠️ API: Sync Failure: {e}")

    async def _mount_cogs(self):
        """Recursively loads all modules from the /cogs directory."""
        if not os.path.exists('./cogs'):
            logger.warning("SYSTEM: /cogs directory missing. Creating empty path.")
            os.makedirs('./cogs')
            return

        for filename in os.listdir('./cogs'):
            if filename.endswith('.py') and not filename.startswith('__'):
                ext = f'cogs.{filename[:-3]}'
                try:
                    await self.load_extension(ext)
                    logger.info(f"✅ SUBSYSTEM: Loaded {ext}")
                except Exception:
                    logger.error(f"❌ SUBSYSTEM: Failed {ext}\n{traceback.format_exc()}")

    async def on_ready(self):
        """Post-connection stabilization routine."""
        uptime = round(time.time() - self.start_time, 2)
        logger.info("-" * 60)
        logger.info(f"SYSTEM ONLINE: {self.user} (ID: {self.user.id})")
        logger.info(f"STABILIZATION TIME: {uptime}s")
        logger.info("-" * 60)

        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="over Licensed Assets | /license"
            )
        )

    async def on_message(self, message: discord.Message):
        """Primary traffic controller and License Enforcement Gate."""
        if message.author.bot or not message.guild:
            return

        # 1. AI Mention Handling (The "Smart" Admin Interface)
        if self.user.mentioned_in(message) and not message.mention_everyone:
            # MANDATORY LICENSE CHECK
            is_licensed = await LicenseManager.has_access(message.guild.id)
            if not is_licensed:
                embed = discord.Embed(
                    title="🛡️ KLAUD: SYSTEM LOCKED",
                    description="This server is not authorized to use AI Protocols. Redeeming a License is required.",
                    color=discord.Color.red()
                )
                return await message.reply(embed=embed)

            # Route to Neural Processor
            await self.execute_neural_intent(message)

        # 2. Regular Command Processing
        await self.process_commands(message)

    async def execute_neural_intent(self, message: discord.Message):
        """Processes AI results into server-side actions."""
        async with message.channel.typing():
            try:
                # 10 second timeout for neural response
                intent = await asyncio.wait_for(
                    gemini_ai.parse_admin_intent(message.content),
                    timeout=12.0
                )
                
                action = intent.get('action')
                self.processed_intents += 1

                # ACTION: CREATE CHANNELS
                if action == 'create_channels':
                    count = intent.get('count', 1)
                    name = intent.get('base_name', 'channel')
                    for i in range(count):
                        await message.guild.create_text_channel(f"{name}-{i+1}")
                    await message.reply(f"✅ **Protocol Success:** Created {count} channels.")

                # ACTION: WIPE SERVER
                elif action == 'delete_channels':
                    await message.reply("⚠️ **NUCLEAR PROTOCOL:** Deleting all channels in 5 seconds...")
                    await asyncio.sleep(5)
                    for channel in message.guild.channels:
                        try: await channel.delete()
                        except: continue
                    # Final feedback in a new channel
                    new_chan = await message.guild.create_text_channel("system-logs")
                    await new_chan.send("✅ **Server Purge Complete.**")

                # ACTION: SETUP ROBLOX TRADING
                elif action == 'setup_server':
                    await message.reply("🛠️ **Setup Sequence:** Building Roblox Trading Template...")
                    categories = ["INFORMATION", "TRADING ROOM", "LOGS"]
                    for cat_name in categories:
                        cat = await message.guild.create_category(cat_name)
                        if cat_name == "INFORMATION":
                            await cat.create_text_channel("rules")
                            await cat.create_text_channel("announcements")
                        elif cat_name == "TRADING ROOM":
                            await cat.create_text_channel("trading")
                            await cat.create_text_channel("vouch-proofs")
                        elif cat_name == "LOGS":
                            await cat.create_text_channel("middleman-logs")
                    await message.reply("✅ **Template Complete.** Roblox Trading Server is ready.")

                else:
                    await message.reply("🧠 **KLAUD AI:** I heard you, but no administrative intent was identified.")

            except asyncio.TimeoutError:
                await message.reply("⏳ **Neural Timeout:** The AI failed to respond in time.")
            except Exception as e:
                logger.error(f"Neural Intent Fault: {e}")
                await message.reply("⚠️ **Neural Fault:** An error occurred during protocol execution.")

# --- LAUNCHER ---
if __name__ == "__main__":
    bot = KlaudNinja()
    token = os.getenv("DISCORD_TOKEN")
    
    async def run_main():
        async with bot:
            try:
                await bot.start(token)
            except Exception:
                logger.error(f"FATAL KERNEL ERROR:\n{traceback.format_exc()}")

    try:
        asyncio.run(run_main())
    except KeyboardInterrupt:
        logger.info("System Offline via Manual Interrupt.")
