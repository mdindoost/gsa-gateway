"""Slash command: /contact — look up GSA officers and campus offices."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.services.moderation import is_channel_allowed

logger = logging.getLogger(__name__)

NJIT_RED = discord.Color.from_str("#CC0000")


class ContactCog(commands.Cog, name="Contact"):
    """Handles /contact — directory lookup."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="contact",
        description="Find contact info for a GSA officer or NJIT office.",
    )
    @app_commands.describe(
        role=(
            "Role to look up (e.g. 'VP Academic Affairs', 'Counseling Center'). "
            "Omit to see all roles."
        )
    )
    async def contact(
        self,
        interaction: discord.Interaction,
        role: str | None = None,
    ) -> None:
        """Display contact info from the contacts directory."""
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

        all_contacts: dict = self.bot.kb.contacts  # type: ignore[attr-defined]

        if not role:
            # List all available roles
            embed = discord.Embed(
                title="📋  GSA & Campus Directory",
                color=NJIT_RED,
                description="Use `/contact <role>` for details on a specific contact.",
            )
            for key, c in all_contacts.items():
                embed.add_field(name=f"👤  {c.role}", value=c.name, inline=True)
            embed.set_footer(text="GSA Gateway · Connecting graduate students")
            await interaction.response.send_message(embed=embed)
            return

        result = self.bot.search_svc.search_contacts(role)  # type: ignore[attr-defined]
        if not result:
            roles_list = ", ".join(c.role for c in all_contacts.values())
            await interaction.response.send_message(
                f"I couldn't find a contact matching **{role}**.\n"
                f"Available roles: `{roles_list}`",
                ephemeral=True,
            )
            return

        contact, score = result
        embed = discord.Embed(
            title=f"👤  {contact.role}",
            color=NJIT_RED,
        )
        embed.add_field(name="Name", value=contact.name, inline=True)
        embed.add_field(name="Email", value=contact.email, inline=True)
        embed.add_field(name="Office", value=contact.office, inline=False)
        embed.add_field(name="Office Hours", value=contact.hours, inline=True)
        if contact.notes:
            embed.add_field(name="Notes", value=contact.notes, inline=False)
        embed.set_footer(text="GSA Gateway · Questions? Try /ask")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Extension entry point."""
    await bot.add_cog(ContactCog(bot))
