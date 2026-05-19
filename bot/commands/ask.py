"""Slash command: /ask — search the knowledge base, enhanced by Ollama when enabled."""

import contextlib
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.services.moderation import is_channel_allowed
from bot.services.search import MIN_CONFIDENCE, OLLAMA_MIN_CONFIDENCE

logger = logging.getLogger(__name__)

FALLBACK = (
    "I'm not fully sure about that one. For the most accurate answer, "
    "please reach out to a GSA officer with `/contact` or visit us during "
    "office hours — Campus Center 110A, weekdays 11 AM–5 PM."
)
AI_FOOTER = "Answer generated from GSA knowledge base"
NJIT_RED = discord.Color.from_str("#CC0000")


class AskCog(commands.Cog, name="Ask"):
    """Handles /ask — knowledge-base question answering."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="ask",
        description="Ask a question about GSA, NJIT resources, or graduate student life.",
    )
    @app_commands.describe(question="Your question (e.g. 'Who are the GSA officers?')")
    async def ask(self, interaction: discord.Interaction, question: str) -> None:
        """Search the knowledge base and return the best answer."""
        if not is_channel_allowed(interaction.channel, config.allowed_channels):
            await interaction.response.send_message(
                "I'm not active in this channel. Please use a designated GSA channel.",
                ephemeral=True,
            )
            return

        if not self.bot.rate_limiter.is_allowed(interaction.user.id):  # type: ignore[attr-defined]
            retry = self.bot.rate_limiter.get_retry_after(interaction.user.id)  # type: ignore[attr-defined]
            await interaction.response.send_message(
                f"You're sending commands a bit quickly! "
                f"Please wait **{retry:.0f} seconds** and try again.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        ollama = getattr(self.bot, "ollama", None)
        use_ollama = config.ollama_enabled and ollama is not None

        if use_ollama:
            # ── Ollama path: lower threshold, always pass context to AI ───────
            # Ollama's system prompt handles uncertainty — the search threshold
            # should not be the gatekeeper here.
            results = self.bot.search_svc.search(  # type: ignore[attr-defined]
                question, limit=4, min_confidence=OLLAMA_MIN_CONFIDENCE
            )

            self.bot.db.log_question(  # type: ignore[attr-defined]
                user_id=interaction.user.id,
                question=question,
                matched_topic=results[0].text if results else None,
                confidence=results[0].score if results else 0.0,
                guild_id=interaction.guild_id,
            )

            if not results:
                await interaction.followup.send(FALLBACK)
                return

            context_chunks = [f"Topic: {r.text}\nInfo: {r.content}" for r in results[:4]]
            chan = interaction.channel
            typing_ctx = chan.typing() if chan is not None else contextlib.nullcontext()
            async with typing_ctx:
                ai_text = await ollama.generate_answer(question, context_chunks)

            if ai_text:
                embed = discord.Embed(title="GSA Knowledge Base", color=NJIT_RED)
                embed.add_field(name="Your Question", value=question[:256], inline=False)
                embed.add_field(name="Answer", value=ai_text[:1000], inline=False)
                best = results[0]
                embed.set_footer(
                    text=f"💡 {AI_FOOTER} · {best.section} · {best.score:.0f}% match"
                )
                await interaction.followup.send(embed=embed)
            else:
                # Ollama failed — fall back to best raw KB text
                best = results[0]
                embed = discord.Embed(title="GSA Knowledge Base", color=NJIT_RED)
                embed.add_field(name="Your Question", value=question[:256], inline=False)
                embed.add_field(name="Answer", value=best.content[:1000], inline=False)
                embed.set_footer(
                    text=f"Source: {best.section} · Confidence: {best.score:.0f}%"
                )
                await interaction.followup.send(embed=embed)

        else:
            # ── Raw KB path: strict threshold, show direct text ───────────────
            results = self.bot.search_svc.search(  # type: ignore[attr-defined]
                question, limit=3, min_confidence=MIN_CONFIDENCE
            )

            self.bot.db.log_question(  # type: ignore[attr-defined]
                user_id=interaction.user.id,
                question=question,
                matched_topic=results[0].text if results else None,
                confidence=results[0].score if results else 0.0,
                guild_id=interaction.guild_id,
            )

            if not results:
                await interaction.followup.send(FALLBACK)
                return

            best = results[0]
            embed = discord.Embed(title="GSA Knowledge Base", color=NJIT_RED)
            embed.add_field(name="Your Question", value=question[:256], inline=False)
            embed.add_field(name="Answer", value=best.content[:1000], inline=False)
            embed.set_footer(
                text=f"Source: {best.section} · Confidence: {best.score:.0f}%"
            )

            if len(results) > 1:
                related = "\n".join(f"• {r.text} ({r.score:.0f}%)" for r in results[1:])
                embed.add_field(name="Related Topics", value=related, inline=False)

            await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Extension entry point."""
    await bot.add_cog(AskCog(bot))
