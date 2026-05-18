"""Slash command: /feedback — submit anonymous feedback."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.services.moderation import is_channel_allowed

logger = logging.getLogger(__name__)

NJIT_RED = discord.Color.from_str("#CC0000")


class FeedbackCog(commands.Cog, name="Feedback"):
    """Handles /feedback — stores student feedback privately."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="feedback",
        description="Send anonymous feedback to GSA officers.",
    )
    @app_commands.describe(
        message="Your feedback, suggestion, or concern (kept private)."
    )
    async def feedback(self, interaction: discord.Interaction, message: str) -> None:
        """Store feedback anonymously in the database."""
        if not is_channel_allowed(interaction.channel, config.allowed_channels):
            await interaction.response.send_message(
                "Please use a designated GSA channel for this command.", ephemeral=True
            )
            return

        if not self.bot.rate_limiter.is_allowed(interaction.user.id):  # type: ignore[attr-defined]
            retry = self.bot.rate_limiter.get_retry_after(interaction.user.id)  # type: ignore[attr-defined]
            await interaction.response.send_message(
                f"Slow down a bit! Try again in **{retry:.0f}s**.", ephemeral=True
            )
            return

        if len(message.strip()) < 5:
            await interaction.response.send_message(
                "Please provide a more detailed message (at least 5 characters).",
                ephemeral=True,
            )
            return

        row_id = self.bot.db.log_feedback(  # type: ignore[attr-defined]
            user_id=interaction.user.id,
            message=message.strip(),
            guild_id=interaction.guild_id,
        )

        embed = discord.Embed(
            title="💬  Feedback Received",
            color=NJIT_RED,
            description=(
                "Thank you for taking the time to share your thoughts! "
                "Your feedback is private — only GSA officers can review it. "
                "Your Discord ID is **never stored in plain text**."
            ),
        )
        embed.add_field(name="Reference #", value=str(row_id), inline=True)
        embed.set_footer(text="GSA Gateway · Your voice, amplified.")

        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.info("Feedback #%d submitted", row_id)


async def setup(bot: commands.Bot) -> None:
    """Extension entry point."""
    await bot.add_cog(FeedbackCog(bot))
