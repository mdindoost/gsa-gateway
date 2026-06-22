"""Discord feedback UI — 👍 / 👎 / 🔄 buttons attached to every bot response."""

from __future__ import annotations

import difflib
import logging
from typing import Optional

import discord

from bot.core.message_handler import MessageRequest
from bot.services.intent_detector import INTENT_QUESTION

logger = logging.getLogger(__name__)

NJIT_RED = discord.Color.from_str("#CC0000")
_SIMILARITY_THRESHOLD = 0.90


class DetailView(discord.ui.View):
    """Ephemeral follow-up after 👎 — captures WHY the answer was bad."""

    def __init__(self, question_id: int, user_id: int, bot) -> None:
        super().__init__(timeout=300)
        self.question_id = question_id
        self.user_id = user_id
        self.bot = bot

    async def _log_and_dismiss(
        self, interaction: discord.Interaction, detail: str
    ) -> None:
        try:
            self.bot.db.log_feedback_rating(
                question_id=self.question_id,
                user_id=self.user_id,
                rating="thumbs_down",
                platform="discord",
                detail=detail,
            )
        except Exception as exc:
            logger.error("DetailView log_feedback_rating error: %s", exc)

        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(
            content="Thanks for the detailed feedback! This helps us improve. 🙏",
            view=self,
        )

    @discord.ui.button(label="Wrong info", style=discord.ButtonStyle.secondary)
    async def wrong_info(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._log_and_dismiss(interaction, "wrong_info")

    @discord.ui.button(label="Incomplete", style=discord.ButtonStyle.secondary)
    async def incomplete(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._log_and_dismiss(interaction, "incomplete")

    @discord.ui.button(label="Off topic", style=discord.ButtonStyle.secondary)
    async def off_topic(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._log_and_dismiss(interaction, "off_topic")


class FeedbackView(discord.ui.View):
    """Three-button row (👍 / 👎 / 🔄) attached to every bot response.

    Only the original asker can interact.  Buttons are disabled after first
    meaningful click.  Timeout is 72 hours; after that they silently expire.
    """

    def __init__(
        self,
        question_id: int,
        asker_id: int,
        question_text: str,
        answer_text: str,
        bot,
        guild_id: Optional[int] = None,
    ) -> None:
        super().__init__(timeout=259200)  # 72 hours
        self.question_id = question_id
        self.asker_id = asker_id
        self.question_text = question_text
        self.answer_text = answer_text
        self.bot = bot
        self.guild_id = guild_id

    def _is_asker(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.asker_id

    async def _disable_buttons(self, message: discord.Message) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        try:
            await message.edit(view=self)
        except Exception as exc:
            logger.warning("FeedbackView: could not disable buttons: %s", exc)

    @discord.ui.button(label="👍 Helpful", style=discord.ButtonStyle.success)
    async def thumbs_up(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not self._is_asker(interaction):
            await interaction.response.send_message(
                "These buttons are for the person who asked.", ephemeral=True
            )
            return

        try:
            self.bot.db.log_feedback_rating(
                question_id=self.question_id,
                user_id=interaction.user.id,
                rating="thumbs_up",
                platform="discord",
            )
        except Exception as exc:
            logger.error("FeedbackView thumbs_up log error: %s", exc)

        await self._disable_buttons(interaction.message)
        await interaction.response.send_message("Thanks for the feedback! 👍", ephemeral=True)

    @discord.ui.button(label="👎 Not helpful", style=discord.ButtonStyle.danger)
    async def thumbs_down(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not self._is_asker(interaction):
            await interaction.response.send_message(
                "These buttons are for the person who asked.", ephemeral=True
            )
            return

        try:
            self.bot.db.log_feedback_rating(
                question_id=self.question_id,
                user_id=interaction.user.id,
                rating="thumbs_down",
                platform="discord",
            )
        except Exception as exc:
            logger.error("FeedbackView thumbs_down log error: %s", exc)

        await self._disable_buttons(interaction.message)
        await interaction.response.send_message(
            "Thanks! What was wrong with the answer?",
            view=DetailView(
                question_id=self.question_id,
                user_id=interaction.user.id,
                bot=self.bot,
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="🔄 Try again", style=discord.ButtonStyle.secondary)
    async def retry(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not self._is_asker(interaction):
            await interaction.response.send_message(
                "These buttons are for the person who asked.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)

        message_handler = getattr(self.bot, "message_handler", None)
        if not message_handler:
            await interaction.followup.send(
                "Retry is not available right now. Please ask your question again."
            )
            return

        req = MessageRequest(
            user_id=str(interaction.user.id),
            text=self.question_text,
            platform="discord",
            guild_id=self.guild_id,
        )

        try:
            new_resp = await message_handler.retry_question(req)
        except Exception as exc:
            logger.error("FeedbackView retry error: %s", exc)
            await interaction.followup.send(
                "Something went wrong with the retry. Please try again or contact "
                "gsa-vpa@njit.edu"
            )
            return

        # Log the regenerate event linking back to the original question
        if new_resp.question_id:
            try:
                self.bot.db.log_feedback_rating(
                    question_id=new_resp.question_id,
                    user_id=interaction.user.id,
                    rating="regenerate",
                    platform="discord",
                    original_question_id=self.question_id,
                )
            except Exception as exc:
                logger.error("FeedbackView retry log error: %s", exc)

        # Similarity check — fall back to rephrase prompt if answer is the same
        similarity = difflib.SequenceMatcher(
            None, self.answer_text, new_resp.text or ""
        ).ratio()

        await self._disable_buttons(interaction.message)

        if similarity > _SIMILARITY_THRESHOLD or not new_resp.text:
            await interaction.followup.send(
                "I got the same answer. Try rephrasing your question or contact "
                "gsa-vpa@njit.edu for direct help."
            )
            return

        embed = discord.Embed(color=NJIT_RED)
        if len(new_resp.text) <= 4096:
            embed.description = new_resp.text
        else:
            embed.description = new_resp.text[:4093] + "..."

        footer_parts = ["💡 GSA Gateway · Kavosh v2.1 · Retry answer"]
        if new_resp.source_note:
            footer_parts.append(f"Source: {new_resp.source_note}")
        if new_resp.used_ai:
            footer_parts.append("AI-generated from official GSA docs")
        embed.set_footer(text=" · ".join(footer_parts))

        new_view: Optional[FeedbackView] = None
        if new_resp.question_id:
            new_view = FeedbackView(
                question_id=new_resp.question_id,
                asker_id=self.asker_id,
                question_text=self.question_text,
                answer_text=new_resp.text,
                bot=self.bot,
                guild_id=self.guild_id,
            )

        await interaction.followup.send(embed=embed, view=new_view)
