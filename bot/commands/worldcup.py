"""Slash command: /worldcup — FIFA World Cup 2026 live scores and schedule."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.services.worldcup_embeds import (
    build_daily_schedule_embed,
    build_standings_embed,
    build_kickoff_embed,
)

logger = logging.getLogger(__name__)


class WorldCupCog(commands.Cog, name="WorldCup"):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="worldcup",
        description="FIFA World Cup 2026 — live scores, schedule, and standings.",
    )
    @app_commands.describe(action="What to show")
    @app_commands.choices(action=[
        app_commands.Choice(name="Today's matches", value="today"),
        app_commands.Choice(name="Live scores",     value="live"),
        app_commands.Choice(name="Upcoming this week", value="upcoming"),
        app_commands.Choice(name="Group standings", value="standings"),
    ])
    async def worldcup(
        self,
        interaction: discord.Interaction,
        action: str = "today",
    ) -> None:
        await interaction.response.defer()

        if not config.football_enabled:
            await interaction.followup.send(
                "⚽ World Cup notifications are not enabled yet. Check back soon!"
            )
            return

        client  = getattr(self.bot, "football_client", None)
        tracker = getattr(self.bot, "worldcup_tracker", None)

        if client is None or tracker is None:
            await interaction.followup.send(
                "⚽ World Cup tracker is not ready yet. Try again in a moment."
            )
            return

        try:
            if action == "today":
                matches = await client.get_todays_matches()
                from bot.services.worldcup_embeds import build_daily_schedule_embed
                embed = build_daily_schedule_embed(matches, tracker)
                await interaction.followup.send(embed=embed)

            elif action == "live":
                matches = await client.get_live_matches()
                if not matches:
                    await interaction.followup.send(
                        "No matches live right now. ⚽\n"
                        "Use `/worldcup today` to see today's schedule."
                    )
                    return
                from bot.services.worldcup_embeds import build_daily_schedule_embed
                embed = build_daily_schedule_embed(matches, tracker)
                embed.title = "🔴 LIVE — World Cup Matches"
                await interaction.followup.send(embed=embed)

            elif action == "upcoming":
                matches = await client.get_upcoming_matches(days=7)
                from bot.services.worldcup_embeds import build_daily_schedule_embed
                embed = build_daily_schedule_embed(matches, tracker)
                embed.title = "📅 Upcoming World Cup Matches (Next 7 Days)"
                await interaction.followup.send(embed=embed)

            elif action == "standings":
                data = await client.get_standings()
                from bot.services.worldcup_embeds import build_standings_embed
                embed = build_standings_embed(data, tracker)
                await interaction.followup.send(embed=embed)

        except Exception as exc:
            logger.error("WorldCup command error: %s", exc, exc_info=True)
            await interaction.followup.send(
                "Something went wrong fetching World Cup data. Try again in a moment."
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WorldCupCog(bot))
