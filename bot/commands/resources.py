"""Slash command: /resources — browse NJIT/GSA resource links."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.services.moderation import is_channel_allowed

logger = logging.getLogger(__name__)

NJIT_RED = discord.Color.from_str("#CC0000")


class ResourcesCog(commands.Cog, name="Resources"):
    """Handles /resources — list categories or resources in a category."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="resources",
        description="Browse NJIT resources by category.",
    )
    @app_commands.describe(
        category=(
            "Resource category (academic, funding, wellness, international, "
            "research, housing, transportation, campus_life). "
            "Omit to see all categories."
        )
    )
    async def resources(
        self,
        interaction: discord.Interaction,
        category: str | None = None,
    ) -> None:
        """Show category list or resources within a specific category."""
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

        all_resources: dict = self.bot.kb.resources  # type: ignore[attr-defined]

        if not category:
            # Show category index
            embed = discord.Embed(
                title="📚  GSA Resource Hub",
                color=NJIT_RED,
                description=(
                    "Use `/resources <category>` to explore a specific topic.\n"
                    "Available categories:"
                ),
            )
            for cat, items in all_resources.items():
                display = cat.replace("_", " ").title()
                embed.add_field(
                    name=f"🔹  {display}",
                    value=f"{len(items)} resource{'s' if len(items) != 1 else ''}",
                    inline=True,
                )
            embed.set_footer(text="GSA Gateway · NJIT Graduate Student Resources")
            await interaction.response.send_message(embed=embed)
            return

        # Fuzzy-match the category name
        from rapidfuzz import process, fuzz

        cat_keys = list(all_resources.keys())
        match = process.extractOne(
            category.lower().replace(" ", "_"),
            cat_keys,
            scorer=fuzz.token_set_ratio,
        )

        if not match or match[1] < 40:
            available = ", ".join(cat_keys)
            await interaction.response.send_message(
                f"Category **{category}** not found.\nAvailable: `{available}`",
                ephemeral=True,
            )
            return

        matched_cat = match[0]
        items = all_resources[matched_cat]
        display_cat = matched_cat.replace("_", " ").title()

        embed = discord.Embed(
            title=f"📚  {display_cat} Resources",
            color=NJIT_RED,
        )
        for res in items:
            value = res.description
            if res.url:
                value += f"\n[🔗 Link]({res.url})"
            embed.add_field(name=res.title, value=value or "—", inline=False)

        embed.set_footer(
            text="GSA Gateway · Run /resources for all categories"
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Extension entry point."""
    await bot.add_cog(ResourcesCog(bot))
