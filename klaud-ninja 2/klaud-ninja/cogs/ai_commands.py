"""
KLAUD-NINJA — AI Commands Cog
Handles @mention-triggered admin AI instructions.
An admin mentions the bot with a natural-language command;
the AI converts it to structured JSON and the interpreter executes it.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from ai.groq_client  import GroqClient
from ai.interpreter  import build_guild_context, execute_plan
from ai.prompts      import GENERAL_SYSTEM, INTERPRETER_SYSTEM, interpreter_user_prompt
from database        import queries
from utils.permissions import is_bot_owner, is_guild_admin

log = logging.getLogger("klaud.ai_commands")


class AICommandsCog(commands.Cog, name="AICommands"):
    """
    Natural-language admin command execution via @mention.

    Workflow:
      1. Admin @mentions the bot with an instruction.
      2. We check admin/owner permission.
      3. Groq AI converts the instruction to a JSON action plan.
      4. The interpreter executes the plan against the Discord API.
      5. Results are shown in a reply embed.
      6. Full interaction logged to Supabase.
    """

    def __init__(self, bot: commands.Bot, groq: GroqClient) -> None:
        self.bot  = bot
        self.groq = groq

    # ── @mention listener ─────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return

        # Must mention the bot and nothing else in terms of target
        if self.bot.user not in message.mentions:
            return

        # Permission gate — admin or bot owner only
        member = message.guild.get_member(message.author.id)
        if not member:
            return
        if not (is_guild_admin(member) or is_bot_owner(member)):
            return

        # Strip the @mention from the content
        instruction = message.content
        for token in (f"<@{self.bot.user.id}>", f"<@!{self.bot.user.id}>"):
            instruction = instruction.replace(token, "").strip()

        if not instruction:
            await message.reply(
                "👋 Mention me with an instruction, e.g.\n"
                "> @Klaud create a category called **Trading** with channels #buy and #sell"
            )
            return

        # Show typing indicator
        async with message.channel.typing():
            await self._handle_instruction(message, instruction)

    # ── Instruction handler ───────────────────────────────────────────────────

    async def _handle_instruction(
        self,
        message:     discord.Message,
        instruction: str,
    ) -> None:
        guild_context = build_guild_context(message.guild)

        # Stage 1: AI interpretation
        plan = await self.groq.complete_json(
            system=INTERPRETER_SYSTEM,
            user=interpreter_user_prompt(instruction, guild_context),
            max_tokens=1024,
            operation="interpreter",
        )

        # Handle unknown / null plan
        if plan is None:
            await message.reply(
                "⚠️ I couldn't process that instruction right now. "
                "The AI may be temporarily unavailable."
            )
            return

        # Single action that is "unknown"
        if isinstance(plan, dict) and plan.get("action") == "unknown":
            explanation = plan.get("explanation", "I couldn't understand that instruction.")
            await message.reply(f"🤔 {explanation}")
            return

        # Stage 2: Execute the plan
        results = await execute_plan(plan, message)

        # Stage 3: Reply with results
        embed = discord.Embed(
            title="🤖 Klaud AI — Executed",
            description="\n".join(results) or "No actions taken.",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Requested by {message.author} • Klaud-Ninja")
        await message.reply(embed=embed)

        # Stage 4: Log to Supabase
        import json as _json
        await queries.log_ai_action(
            guild_id=message.guild.id,
            input_text=instruction,
            ai_response=_json.dumps(plan) if plan else "null",
            executed_action="\n".join(results),
        )

    # ── /klaud-test-ai ────────────────────────────────────────────────────────

    @commands.hybrid_command(
        name="klaud-test-ai",
        description="Test the AI with a free-form question",
    )
    async def klaud_test_ai(self, ctx: commands.Context, *, question: str) -> None:
        """Send a question to the AI and display the response."""
        if not (is_guild_admin(ctx.author) or is_bot_owner(ctx.author)):
            await ctx.send("❌ Admins only.", ephemeral=True)
            return

        async with ctx.typing():
            answer = await self.groq.complete(
                system=GENERAL_SYSTEM,
                user=question,
                max_tokens=512,
                operation="test_ai",
            )

        if answer:
            embed = discord.Embed(
                title="🤖 Klaud AI Response",
                description=answer,
                color=discord.Color.blurple(),
            )
            embed.set_footer(text=f"Asked by {ctx.author}")
            await ctx.send(embed=embed)
        else:
            await ctx.send("⚠️ AI unavailable right now. Check your API key.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    groq: GroqClient = bot.groq  # type: ignore[attr-defined]
    await bot.add_cog(AICommandsCog(bot, groq))
