"""Admin slash commands — all ephemeral, gated behind ADMIN_ROLE_NAME."""

import csv
import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.services.moderation import is_admin
from bot.services.summaries import SummaryService

logger = logging.getLogger(__name__)

NJIT_RED = discord.Color.from_str("#CC0000")
NO_PERMISSION = (
    "🔒 You don't have permission to use this command. "
    "This requires the **{role}** role."
)


def _admin_check(interaction: discord.Interaction) -> bool:
    return is_admin(interaction, config.admin_role_name)


class AdminCog(commands.Cog, name="Admin"):
    """All /admin_* commands for GSA officers."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.summary_svc = SummaryService(bot.db)  # type: ignore[attr-defined]

    # ── /admin_summary ────────────────────────────────────────────────────────

    @app_commands.command(
        name="admin_summary",
        description="[Admin] Weekly summary of initiatives and feedback.",
    )
    async def admin_summary(self, interaction: discord.Interaction) -> None:
        """Post the 7-day summary text (ephemeral)."""
        if not _admin_check(interaction):
            await interaction.response.send_message(
                NO_PERMISSION.format(role=config.admin_role_name), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        summary = self.summary_svc.weekly_summary(days=7)

        self.bot.db.log_admin_action(  # type: ignore[attr-defined]
            officer_id=interaction.user.id,
            action="admin_summary",
            detail=None,
        )

        # Split long summaries into chunks ≤ 2000 chars
        if len(summary) <= 1900:
            await interaction.followup.send(summary, ephemeral=True)
        else:
            chunks = [summary[i : i + 1900] for i in range(0, len(summary), 1900)]
            await interaction.followup.send(chunks[0], ephemeral=True)
            for chunk in chunks[1:]:
                await interaction.followup.send(chunk, ephemeral=True)

    # ── /admin_export ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="admin_export",
        description="[Admin] Export all data as a CSV file attachment.",
    )
    @app_commands.describe(
        table="Table to export: questions, initiatives, or feedback."
    )
    async def admin_export(
        self,
        interaction: discord.Interaction,
        table: str = "initiatives",
    ) -> None:
        """Export a database table as a CSV with no raw user identifiers."""
        if not _admin_check(interaction):
            await interaction.response.send_message(
                NO_PERMISSION.format(role=config.admin_role_name), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        table = table.lower().strip()
        db = self.bot.db  # type: ignore[attr-defined]

        if table == "questions":
            rows = db.get_all_questions()
        elif table == "initiatives":
            rows = db.get_all_initiatives()
        elif table == "feedback":
            rows = db.get_all_feedback()
        else:
            await interaction.followup.send(
                "Unknown table. Choose: `questions`, `initiatives`, or `feedback`.",
                ephemeral=True,
            )
            return

        if not rows:
            await interaction.followup.send(
                f"No data found in **{table}** table.", ephemeral=True
            )
            return

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
        buf.seek(0)

        file = discord.File(
            fp=io.BytesIO(buf.read().encode()),
            filename=f"gsa_{table}_export.csv",
        )

        db.log_admin_action(
            officer_id=interaction.user.id,
            action="admin_export",
            detail=f"table={table} rows={len(rows)}",
        )

        await interaction.followup.send(
            f"✅ Exported **{len(rows)}** rows from `{table}`.",
            file=file,
            ephemeral=True,
        )

    # ── /admin_stats ──────────────────────────────────────────────────────────

    @app_commands.command(
        name="admin_stats",
        description="[Admin] Show engagement stats and top search terms.",
    )
    async def admin_stats(self, interaction: discord.Interaction) -> None:
        """Display aggregate stats in an embed."""
        if not _admin_check(interaction):
            await interaction.response.send_message(
                NO_PERMISSION.format(role=config.admin_role_name), ephemeral=True
            )
            return

        stats = self.bot.db.get_stats()  # type: ignore[attr-defined]

        embed = discord.Embed(title="📊  GSA Gateway Stats", color=NJIT_RED)
        embed.add_field(
            name="Questions", value=str(stats["total_questions"]), inline=True
        )
        embed.add_field(
            name="Initiatives", value=str(stats["total_initiatives"]), inline=True
        )
        embed.add_field(
            name="Feedback Items", value=str(stats["total_feedback"]), inline=True
        )

        if stats["top_topics"]:
            topic_lines = "\n".join(
                f"• {t['matched_topic']} ({t['count']})"
                for t in stats["top_topics"]
            )
            embed.add_field(name="Top Search Topics", value=topic_lines, inline=False)

        self.bot.db.log_admin_action(  # type: ignore[attr-defined]
            officer_id=interaction.user.id, action="admin_stats", detail=None
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /admin_announce ───────────────────────────────────────────────────────

    @app_commands.command(
        name="admin_announce",
        description="[Admin] Send an announcement to a specific channel.",
    )
    @app_commands.describe(
        channel="The channel to post in.",
        message="The announcement text.",
    )
    async def admin_announce(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str,
    ) -> None:
        """Post an announcement as a rich embed to the chosen channel."""
        if not _admin_check(interaction):
            await interaction.response.send_message(
                NO_PERMISSION.format(role=config.admin_role_name), ephemeral=True
            )
            return

        if not message.strip():
            await interaction.response.send_message(
                "Announcement message cannot be empty.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="📢  GSA Announcement",
            description=message.strip(),
            color=NJIT_RED,
        )
        embed.set_footer(text="NJIT Graduate Student Association · GSA Gateway")

        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"I don't have permission to post in {channel.mention}.", ephemeral=True
            )
            return

        self.bot.db.log_admin_action(  # type: ignore[attr-defined]
            officer_id=interaction.user.id,
            action="admin_announce",
            detail=f"channel={channel.name}",
        )
        await interaction.response.send_message(
            f"✅ Announcement posted in {channel.mention}.", ephemeral=True
        )

    # ── /admin_add_event ──────────────────────────────────────────────────────

    @app_commands.command(
        name="admin_add_event",
        description="[Admin] Instructions for adding a new event.",
    )
    async def admin_add_event(self, interaction: discord.Interaction) -> None:
        """Explain how to add events to the YAML file (bot cannot edit files)."""
        if not _admin_check(interaction):
            await interaction.response.send_message(
                NO_PERMISSION.format(role=config.admin_role_name), ephemeral=True
            )
            return

        embed = discord.Embed(
            title="📅  How to Add a New Event",
            color=NJIT_RED,
            description=(
                "Events are stored in `bot/data/events.yml`. "
                "To add a new event, edit that file and restart the bot."
            ),
        )
        embed.add_field(
            name="Step 1 — Edit events.yml",
            value=(
                "Open `bot/data/events.yml` and append a new entry under `events:`.\n"
                "Copy an existing block as a template."
            ),
            inline=False,
        )
        embed.add_field(
            name="Step 2 — Required fields",
            value=(
                "```yaml\n"
                "- name: \"Event Name\"\n"
                "  date: YYYY-MM-DD\n"
                "  time: \"6:00 PM – 8:00 PM\"\n"
                "  location: \"Room, Building\"\n"
                "  description: \"Description text.\"\n"
                "  organizer: \"Your name or committee\"\n"
                "  rsvp_link: \"https://...\"\n"
                "  category: networking\n"
                "```"
            ),
            inline=False,
        )
        embed.add_field(
            name="Step 3 — Sync the website",
            value=(
                "Run `python scripts/export_events_json.py` to update "
                "`website/data/events.json` for the public website."
            ),
            inline=False,
        )
        embed.add_field(
            name="Step 4 — Restart the bot",
            value="The bot reloads `events.yml` at startup automatically.",
            inline=False,
        )
        embed.set_footer(text="GSA Gateway Admin Guide")

        self.bot.db.log_admin_action(  # type: ignore[attr-defined]
            officer_id=interaction.user.id, action="admin_add_event", detail=None
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Extension entry point."""
    await bot.add_cog(AdminCog(bot))
