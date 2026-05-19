"""Admin slash commands — all ephemeral, gated behind ADMIN_ROLE_NAME."""

import csv
import io
import logging
from datetime import datetime, timezone
from pathlib import Path

import discord
import yaml
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


# ── Add-event modal ────────────────────────────────────────────────────────────

class AddEventModal(discord.ui.Modal, title="Add New GSA Event"):
    """Five-field modal for creating a new event with auto-announcement."""

    event_name = discord.ui.TextInput(
        label="Event Name",
        placeholder="GSA Friday Happy Hour",
        max_length=100,
        required=True,
    )
    date = discord.ui.TextInput(
        label="Date (YYYY-MM-DD)",
        placeholder="2026-07-04",
        max_length=10,
        required=True,
    )
    time_and_location = discord.ui.TextInput(
        label="Time & Location  (use | as separator)",
        placeholder="4:00 PM - 7:00 PM | Highlander Pub, Campus Center 3rd Floor",
        max_length=200,
        required=False,
    )
    description = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        placeholder="What is this event about? Who should attend?",
        max_length=1000,
        required=False,
    )
    tags_and_rsvp = discord.ui.TextInput(
        label="Category & RSVP Link  (use | as separator)",
        placeholder="food, social | https://rsvp.link",
        max_length=300,
        required=False,
    )

    def __init__(self, bot: commands.Bot, officer_id: int) -> None:
        super().__init__()
        self._bot = bot
        self._officer_id = officer_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        # ── Parse fields ───────────────────────────────────────────────────────
        name = self.event_name.value.strip()
        date_str = self.date.value.strip()

        # Parse "time | location"
        tl = self.time_and_location.value.strip()
        if "|" in tl:
            parts = tl.split("|", 1)
            time_val = parts[0].strip() or "TBD"
            location = parts[1].strip() or "TBD"
        else:
            time_val = tl or "TBD"
            location = "TBD"

        desc = self.description.value.strip()

        # Parse "category, category | https://rsvp"
        tr = self.tags_and_rsvp.value.strip()
        if "|" in tr:
            parts = tr.split("|", 1)
            tags_str = parts[0].strip()
            rsvp_link = parts[1].strip()
        else:
            tags_str = tr
            rsvp_link = ""

        categories = [c.strip().lower() for c in tags_str.split(",") if c.strip()]
        primary_category = categories[0] if categories else "general"

        # ── Validate date ──────────────────────────────────────────────────────
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            await interaction.followup.send(
                f"❌ Invalid date format: **{date_str}**\n"
                "Please use YYYY-MM-DD (e.g. `2026-07-04`).",
                ephemeral=True,
            )
            return

        # ── Save to SQLite ─────────────────────────────────────────────────────
        db = self._bot.db  # type: ignore[attr-defined]
        event_id = db.add_event(
            name=name,
            date=date_str,
            time=time_val,
            location=location,
            description=desc,
            organizer="GSA",
            rsvp_link=rsvp_link,
            category=primary_category,
            officer_id=self._officer_id,
        )

        # ── Append to events.yml ───────────────────────────────────────────────
        _append_to_events_yml(
            name=name,
            date=date_str,
            time=time_val,
            location=location,
            description=desc,
            organizer="GSA",
            rsvp_link=rsvp_link,
            category=primary_category,
        )

        # Reload KB so /ask and /events see the new entry immediately
        try:
            self._bot.kb.load()  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("KB reload after add_event failed: %s", exc)

        # ── Export events.json ─────────────────────────────────────────────────
        try:
            from scripts.export_events_json import export_events_to_json
            export_events_to_json(db=db)
        except Exception as exc:
            logger.warning("events.json export failed: %s", exc)

        # ── Post announcement embeds ───────────────────────────────────────────
        from bot.services.announcements import format_event_announcement
        from bot.services.channels import (
            get_announcement_channel,
            get_channels_for_categories,
        )

        event_dict = {
            "name": name, "date": date_str, "time": time_val,
            "location": location, "description": desc,
            "organizer": "GSA", "rsvp_link": rsvp_link,
            "category": primary_category,
        }
        embed = format_event_announcement(event_dict, "new")

        guild = interaction.guild
        channels_posted: list[str] = []

        if guild is not None:
            # Category-specific channels
            cat_channels = get_channels_for_categories(guild, categories)
            for ch in cat_channels:
                try:
                    await ch.send(embed=embed)
                    channels_posted.append(f"#{ch.name}")
                except discord.Forbidden:
                    logger.warning("No permission to post in #%s", ch.name)

            # Always post to #gsa-announcements
            ann_ch = get_announcement_channel(guild)
            if ann_ch and ann_ch not in cat_channels:
                try:
                    await ann_ch.send(embed=embed)
                    channels_posted.append(f"#{ann_ch.name}")
                except discord.Forbidden:
                    logger.warning("No permission to post in #%s", ann_ch.name)

        # Mark announcement sent
        if channels_posted:
            db.mark_announcement_sent(event_id, ", ".join(channels_posted))

        db.log_event_action(name, "created", self._officer_id)
        db.log_admin_action(
            officer_id=self._officer_id,
            action="admin_add_event",
            detail=f"name={name} date={date_str} channels={','.join(channels_posted)}",
        )

        # ── Confirm to officer ─────────────────────────────────────────────────
        posted_text = (
            "Posted to: " + ", ".join(channels_posted)
            if channels_posted
            else "No announcement channels found — create them in Discord first."
        )
        await interaction.followup.send(
            f"✅ **{name}** added!\n"
            f"{posted_text}\n"
            f"Reminders scheduled for **7 days**, **1 day**, and **1 hour** before.",
            ephemeral=True,
        )

    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        logger.error("AddEventModal error: %s", error, exc_info=error)
        try:
            await interaction.followup.send(
                "❌ Something went wrong saving the event. Please try again.",
                ephemeral=True,
            )
        except Exception:
            pass


# ── YAML helper ────────────────────────────────────────────────────────────────

def _append_to_events_yml(
    name: str, date: str, time: str, location: str,
    description: str, organizer: str, rsvp_link: str, category: str,
) -> None:
    """Load events.yml, append the new entry, and write it back."""
    events_yml = config.data_dir / "events.yml"
    try:
        with open(events_yml, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except OSError:
        data = {}

    if "events" not in data or not isinstance(data["events"], list):
        data["events"] = []

    data["events"].append({
        "name":        name,
        "date":        date,
        "time":        time,
        "location":    location,
        "description": description,
        "organizer":   organizer,
        "rsvp_link":   rsvp_link,
        "category":    category,
    })

    with open(events_yml, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True)
    logger.info("Appended '%s' to events.yml", name)


# ── Cog ────────────────────────────────────────────────────────────────────────

class AdminCog(commands.Cog, name="Admin"):
    """All /admin_* commands for GSA officers."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.summary_svc = SummaryService(bot.db)  # type: ignore[attr-defined]

    # ── /admin_summary ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="admin_summary",
        description="[Admin] Weekly summary of initiatives and feedback.",
    )
    async def admin_summary(self, interaction: discord.Interaction) -> None:
        if not _admin_check(interaction):
            await interaction.response.send_message(
                NO_PERMISSION.format(role=config.admin_role_name), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        ollama = getattr(self.bot, "ollama", None)
        if config.ollama_enabled and ollama is not None:
            summary = await self.summary_svc.generate_ai_summary(ollama, days=7)
        else:
            summary = self.summary_svc.weekly_summary(days=7)

        self.bot.db.log_admin_action(  # type: ignore[attr-defined]
            officer_id=interaction.user.id,
            action="admin_summary",
            detail=f"ai={'yes' if config.ollama_enabled and ollama else 'no'}",
        )

        if len(summary) <= 1900:
            await interaction.followup.send(summary, ephemeral=True)
        else:
            chunks = [summary[i : i + 1900] for i in range(0, len(summary), 1900)]
            await interaction.followup.send(chunks[0], ephemeral=True)
            for chunk in chunks[1:]:
                await interaction.followup.send(chunk, ephemeral=True)

    # ── /admin_export ──────────────────────────────────────────────────────────

    @app_commands.command(
        name="admin_export",
        description="[Admin] Export all data as a CSV file attachment.",
    )
    @app_commands.describe(table="Table to export: questions, initiatives, or feedback.")
    async def admin_export(
        self,
        interaction: discord.Interaction,
        table: str = "initiatives",
    ) -> None:
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

    # ── /admin_stats ───────────────────────────────────────────────────────────

    @app_commands.command(
        name="admin_stats",
        description="[Admin] Show engagement stats and top search terms.",
    )
    async def admin_stats(self, interaction: discord.Interaction) -> None:
        if not _admin_check(interaction):
            await interaction.response.send_message(
                NO_PERMISSION.format(role=config.admin_role_name), ephemeral=True
            )
            return

        stats = self.bot.db.get_stats()  # type: ignore[attr-defined]

        embed = discord.Embed(title="📊  GSA Gateway Stats", color=NJIT_RED)
        embed.add_field(name="Questions",    value=str(stats["total_questions"]),  inline=True)
        embed.add_field(name="Initiatives",  value=str(stats["total_initiatives"]), inline=True)
        embed.add_field(name="Feedback Items", value=str(stats["total_feedback"]), inline=True)

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

    # ── /admin_announce ────────────────────────────────────────────────────────

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

    # ── /admin_add_event ───────────────────────────────────────────────────────

    @app_commands.command(
        name="admin_add_event",
        description="[Admin] Add a new event via a form — posts announcement automatically.",
    )
    async def admin_add_event(self, interaction: discord.Interaction) -> None:
        """Open the AddEventModal form for the officer."""
        if not _admin_check(interaction):
            await interaction.response.send_message(
                NO_PERMISSION.format(role=config.admin_role_name), ephemeral=True
            )
            return

        modal = AddEventModal(bot=self.bot, officer_id=interaction.user.id)
        await interaction.response.send_modal(modal)


async def setup(bot: commands.Bot) -> None:
    """Extension entry point."""
    await bot.add_cog(AdminCog(bot))
