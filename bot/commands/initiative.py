"""Slash command: /initiative — submit a student initiative via Discord Modal."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.services.moderation import is_channel_allowed

logger = logging.getLogger(__name__)

NJIT_RED = discord.Color.from_str("#CC0000")

VALID_CATEGORIES = [
    "academic",
    "social",
    "career",
    "wellness",
    "funding",
    "international",
    "research",
    "housing",
    "other",
]

CATEGORY_HINT = ", ".join(VALID_CATEGORIES)


def _normalise_category(raw: str) -> str:
    """Map free-text input to a known category, falling back to 'other'."""
    cleaned = raw.strip().lower()
    for cat in VALID_CATEGORIES:
        if cat in cleaned or cleaned in cat:
            return cat
    return "other"


def _normalise_bool(raw: str) -> bool:
    """Interpret yes/no/true/false text input."""
    return raw.strip().lower() in {"yes", "y", "true", "1"}


class InitiativeModal(discord.ui.Modal, title="Submit a Student Initiative"):
    """Discord Modal collecting initiative details from the student."""

    initiative_title = discord.ui.TextInput(
        label="Initiative Title",
        placeholder="E.g. Weekly Graduate Study Groups",
        max_length=100,
        required=True,
    )
    description = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        placeholder="Describe your idea, its goals, and the students it would benefit.",
        max_length=1000,
        required=True,
    )
    category = discord.ui.TextInput(
        label="Category",
        placeholder=f"One of: {CATEGORY_HINT}",
        max_length=50,
        required=True,
    )
    include_contact = discord.ui.TextInput(
        label="Include your contact info? (yes / no)",
        placeholder="Type 'yes' if you'd like GSA to follow up with you.",
        max_length=10,
        required=True,
    )
    contact_info = discord.ui.TextInput(
        label="Contact Info (email/Discord — if yes above)",
        placeholder="Leave blank if you answered 'no' above.",
        max_length=200,
        required=False,
    )

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        want_contact = _normalise_bool(str(self.include_contact.value))
        cat = _normalise_category(str(self.category.value))
        contact_text = str(self.contact_info.value).strip() if want_contact else None

        row_id = self.bot.db.log_initiative(  # type: ignore[attr-defined]
            user_id=interaction.user.id,
            title=str(self.initiative_title.value).strip(),
            description=str(self.description.value).strip(),
            category=cat,
            include_contact=want_contact,
            contact_info=contact_text,
            guild_id=interaction.guild_id,
        )

        contact_note = (
            "We'll follow up using the contact info you provided."
            if want_contact
            else "Your submission is anonymous — your identity has not been stored."
        )

        embed = discord.Embed(
            title="✅  Initiative Received!",
            color=NJIT_RED,
            description=(
                f"Thank you for sharing your idea with GSA! "
                f"A GSA officer will review it shortly.\n\n{contact_note}"
            ),
        )
        embed.add_field(name="Title", value=str(self.initiative_title.value), inline=False)
        embed.add_field(name="Category", value=cat.title(), inline=True)
        embed.add_field(name="Reference #", value=str(row_id), inline=True)
        embed.set_footer(text="GSA Gateway · Your voice, amplified.")

        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.info("Initiative #%d submitted (category=%s)", row_id, cat)

    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        logger.error("Initiative modal error: %s", error, exc_info=error)
        await interaction.response.send_message(
            "Something went wrong while submitting your initiative. "
            "Please try again or contact a GSA officer.",
            ephemeral=True,
        )


class InitiativeCog(commands.Cog, name="Initiative"):
    """Handles /initiative — open the submission modal."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="initiative",
        description="Submit an idea or initiative to the GSA.",
    )
    async def initiative(self, interaction: discord.Interaction) -> None:
        """Open the initiative submission modal."""
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

        await interaction.response.send_modal(InitiativeModal(self.bot))


async def setup(bot: commands.Bot) -> None:
    """Extension entry point."""
    await bot.add_cog(InitiativeCog(bot))
