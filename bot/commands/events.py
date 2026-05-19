"""Slash commands: /events and /event — browse GSA events."""

import logging
from datetime import date as _date

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.services.moderation import is_channel_allowed

logger = logging.getLogger(__name__)

NJIT_RED = discord.Color.from_str("#CC0000")
NO_EVENTS_MSG = (
    "There are no upcoming GSA events right now. "
    "Check back soon or follow our announcements channel!"
)


def _event_embed(event) -> discord.Embed:
    """Build a rich embed for a single event."""
    embed = discord.Embed(
        title=f"📅  {event.name}",
        color=NJIT_RED,
        description=event.description[:1024] if event.description else "",
    )
    embed.add_field(name="Date", value=event.date, inline=True)
    embed.add_field(name="Time", value=event.time, inline=True)
    embed.add_field(name="Location", value=event.location, inline=False)
    embed.add_field(name="Organizer", value=event.organizer, inline=True)
    embed.add_field(name="Category", value=event.category.title(), inline=True)
    if event.rsvp_link:
        embed.add_field(name="RSVP", value=event.rsvp_link, inline=False)
    embed.set_footer(text="GSA Gateway · /events for full list")
    return embed


class EventsCog(commands.Cog, name="Events"):
    """Handles /events and /event commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="events",
        description="List all upcoming GSA events.",
    )
    async def events(self, interaction: discord.Interaction) -> None:
        """Show all upcoming events sorted by date."""
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

        today = _date.today().isoformat()
        upcoming = [
            e for e in self.bot.kb.get_upcoming_events()  # type: ignore[attr-defined]
            if e.date >= today
        ]
        if not upcoming:
            await interaction.response.send_message(NO_EVENTS_MSG)
            return

        embed = discord.Embed(
            title="🎓  Upcoming GSA Events",
            color=NJIT_RED,
            description="Here's what's coming up! Use `/event <name>` for full details.",
        )
        for ev in upcoming[:10]:
            embed.add_field(
                name=f"📅  {ev.name}",
                value=(
                    f"**{ev.date}** · {ev.time}\n"
                    f"📍 {ev.location}\n"
                    f"_{ev.description[:80]}…_" if len(ev.description) > 80
                    else f"**{ev.date}** · {ev.time}\n📍 {ev.location}"
                ),
                inline=False,
            )
        embed.set_footer(text="GSA Gateway · Use /event <name> for full details")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="event",
        description="Get full details for a specific GSA event.",
    )
    @app_commands.describe(name="Name of the event (partial names work)")
    async def event(self, interaction: discord.Interaction, name: str) -> None:
        """Fuzzy-match an event by name and display its full details."""
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

        matches = self.bot.search_svc.search_events(name)  # type: ignore[attr-defined]
        if not matches:
            await interaction.response.send_message(
                f"I couldn't find an event matching **{name}**. "
                "Try `/events` to see all upcoming events.",
                ephemeral=True,
            )
            return

        best_event, score = matches[0]
        embed = _event_embed(best_event)
        if score < 90 and len(matches) > 1:
            alternatives = ", ".join(f"_{m[0].name}_" for m in matches[1:])
            embed.add_field(
                name="Did you mean one of these?",
                value=alternatives,
                inline=False,
            )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Extension entry point."""
    await bot.add_cog(EventsCog(bot))
