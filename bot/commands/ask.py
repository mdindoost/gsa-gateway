"""Slash command: /ask — RAG-powered question answering."""

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.services.food_detector import format_food_response, get_food_events, is_food_query
from bot.services.moderation import is_channel_allowed
from bot.services.retriever import SOURCE_FRIENDLY_NAMES
from bot.ui.feedback import FeedbackView

logger = logging.getLogger(__name__)

FALLBACK = (
    "I'm not fully sure about that one. For the most accurate answer, "
    "please reach out to a GSA officer with `/contact` or visit us during "
    "office hours — Campus Center 110A, weekdays 11 AM–5 PM."
)
NJIT_RED = discord.Color.from_str("#CC0000")


class AskCog(commands.Cog, name="Ask"):
    """Handles /ask — RAG-powered knowledge-base question answering."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="ask",
        description="Ask a question about GSA, NJIT resources, or graduate student life.",
    )
    @app_commands.describe(question="Your question (e.g. 'Who are the GSA officers?')")
    async def ask(self, interaction: discord.Interaction, question: str) -> None:
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

        # ── Food shortcut ─────────────────────────────────────────────────────
        if is_food_query(question):
            food_events = get_food_events(
                kb=self.bot.kb,  # type: ignore[attr-defined]
                db=self.bot.db,  # type: ignore[attr-defined]
                days_ahead=7,
            )
            question_id = self.bot.db.log_question(  # type: ignore[attr-defined]
                user_id=interaction.user.id,
                question=question,
                matched_topic="food events",
                confidence=100.0,
                guild_id=interaction.guild_id,
            )
            if food_events:
                food_embed = format_food_response(food_events)
                food_view = FeedbackView(
                    question_id=question_id,
                    asker_id=interaction.user.id,
                    question_text=question,
                    answer_text="[Food events listing]",
                    bot=self.bot,
                    guild_id=interaction.guild_id,
                ) if question_id else None
                await interaction.followup.send(embed=food_embed, view=food_view)
            else:
                await interaction.followup.send(
                    "😔 No events with food in the next 7 days right now.\n\n"
                    "Follow **#gsa-food** channel for announcements, or check back soon — "
                    "GSA regularly hosts events with free food and refreshments! 🍕"
                )
            return

        # ── RAG pipeline ──────────────────────────────────────────────────────
        retriever = getattr(self.bot, "retriever", None)
        ollama = getattr(self.bot, "ollama", None)
        conversation_manager = getattr(self.bot, "conversation_manager", None)

        user_id = str(interaction.user.id)

        # Get conversation history for this user
        history: list[dict] = []
        if conversation_manager:
            history = conversation_manager.get_history(
                user_id, max_turns=config.conversation_max_turns
            )

        # Retrieve relevant chunks
        chunks = []
        if retriever:
            chunks = await retriever.retrieve(
                query=question,
                conversation_history=history,
            )

        question_id = self.bot.db.log_question(  # type: ignore[attr-defined]
            user_id=interaction.user.id,
            question=question,
            matched_topic=chunks[0].section_title if chunks else None,
            confidence=chunks[0].relevance_score * 100 if chunks else 0.0,
            guild_id=interaction.guild_id,
        )

        response_text: Optional[str] = None
        used_ollama = False
        source_note: Optional[str] = None

        if chunks and ollama:
            ai_response = await ollama.generate_answer(
                question=question,
                chunks=chunks,
                conversation_history=history,
            )
            if ai_response:
                response_text = ai_response
                source_files = list({c.source_file for c in chunks})
                source_names = [SOURCE_FRIENDLY_NAMES.get(f, f) for f in source_files[:2]]
                source_note = " & ".join(source_names)
                used_ollama = True
            else:
                # Ollama timeout — fallback to best chunk raw text
                best = chunks[0]
                response_text = best.text[:1000]
                source_note = SOURCE_FRIENDLY_NAMES.get(best.source_file, best.source_file)
        elif chunks:
            best = chunks[0]
            response_text = best.text[:1000]
            source_note = SOURCE_FRIENDLY_NAMES.get(best.source_file, best.source_file)

        if not response_text:
            await interaction.followup.send(FALLBACK)
            return

        embed = discord.Embed(title="GSA Knowledge Base", color=NJIT_RED)
        embed.add_field(name="Your Question", value=question[:256], inline=False)
        embed.add_field(name="Answer", value=response_text[:1000], inline=False)

        footer = "💡 GSA Knowledge Base"
        if source_note:
            footer += f" · Source: {source_note}"
        if used_ollama:
            footer += " · AI-generated from official GSA docs"
        embed.set_footer(text=footer)

        rag_view = FeedbackView(
            question_id=question_id,
            asker_id=interaction.user.id,
            question_text=question,
            answer_text=response_text,
            bot=self.bot,
            guild_id=interaction.guild_id,
        ) if question_id else None

        await interaction.followup.send(embed=embed, view=rag_view)

        # Update conversation memory
        if conversation_manager:
            conversation_manager.add_turn(
                user_id=user_id,
                role="user",
                content=question,
                source_files=[],
            )
            conversation_manager.add_turn(
                user_id=user_id,
                role="assistant",
                content=response_text[:500],
                source_files=[c.source_file for c in chunks],
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AskCog(bot))
