"""Slash command: /ask — search the knowledge base."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.services.moderation import is_channel_allowed

logger = logging.getLogger(__name__)

FALLBACK = (
    "I'm not fully sure about that one. For the most accurate answer, "
    "please reach out to a GSA officer with `/contact` or visit us during "
    "office hours. We're always happy to help! 🎓"
)
NJIT_RED = discord.Color.from_str("#CC0000")


class AskCog(commands.Cog, name="Ask"):
    """Handles /ask — knowledge-base question answering."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="ask",
        description="Ask a question about GSA, NJIT resources, or graduate student life.",
    )
    @app_commands.describe(question="Your question (e.g. 'How do I apply for funding?')")
    async def ask(self, interaction: discord.Interaction, question: str) -> None:
        """Fuzzy-search the knowledge base and return the best answer."""
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

        results = self.bot.search_svc.search(question)  # type: ignore[attr-defined]

        if not results or results[0].score < 60:
            self.bot.db.log_question(  # type: ignore[attr-defined]
                user_id=interaction.user.id,
                question=question,
                matched_topic=results[0].text if results else None,
                confidence=results[0].score if results else 0.0,
                guild_id=interaction.guild_id,
            )
            await interaction.followup.send(FALLBACK)
            return

        best = results[0]

        self.bot.db.log_question(  # type: ignore[attr-defined]
            user_id=interaction.user.id,
            question=question,
            matched_topic=best.text,
            confidence=best.score,
            guild_id=interaction.guild_id,
        )

        embed = discord.Embed(title="GSA Knowledge Base", color=NJIT_RED)
        embed.add_field(name="Your Question", value=question[:256], inline=False)
        answer_text = best.content[:1000]

        # Optional Ollama enhancement (never answers without context)
        if config.ollama_enabled and hasattr(self.bot, "ollama"):
            context_chunks = [
                f"Q: {r.text}\nA: {r.content}" for r in results
            ]
            context = "\n\n".join(context_chunks)
            enhanced = await self.bot.ollama.generate_answer(question, context)  # type: ignore[attr-defined]
            if enhanced:
                answer_text = enhanced[:1000]
                embed.set_footer(text=f"AI-assisted · Source: {best.section} · Confidence: {best.score:.0f}%")
            else:
                embed.set_footer(text=f"Source: {best.section} · Confidence: {best.score:.0f}%")
        else:
            embed.set_footer(text=f"Source: {best.section} · Confidence: {best.score:.0f}%")

        embed.add_field(name="Answer", value=answer_text, inline=False)

        if len(results) > 1:
            related = "\n".join(
                f"• {r.text} ({r.score:.0f}%)" for r in results[1:]
            )
            embed.add_field(name="Related Topics", value=related, inline=False)

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Extension entry point."""
    await bot.add_cog(AskCog(bot))
