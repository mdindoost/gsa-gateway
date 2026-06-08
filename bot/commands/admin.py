"""Admin slash commands — all ephemeral, gated behind ADMIN_ROLE_NAME."""

import csv
import datetime
import io
import logging
from pathlib import Path
from typing import Optional

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
        placeholder="food, social | https://rsvp.link  ← add 'food' if free food/drinks!",
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
            datetime.datetime.strptime(date_str, "%Y-%m-%d")
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
        from bot.services.food_detector import format_food_alert_embed

        event_dict = {
            "name": name, "date": date_str, "time": time_val,
            "location": location, "description": desc,
            "organizer": "GSA", "rsvp_link": rsvp_link,
            "category": primary_category,
        }
        embed = format_event_announcement(event_dict, "new")

        guild = interaction.guild

        # Food events get a special alert embed for #gsa-food channel
        food_ch = (
            discord.utils.get(guild.text_channels, name=config.channel_food)
            if "food" in categories and guild is not None
            else None
        )
        food_embed = format_food_alert_embed(event_dict) if food_ch else None
        channels_posted: list[str] = []

        if guild is not None:
            # Category-specific channels
            cat_channels = get_channels_for_categories(guild, categories)
            for ch in cat_channels:
                try:
                    # Food channel gets the special alert embed
                    to_send = food_embed if (food_embed and food_ch and ch.id == food_ch.id) else embed
                    await ch.send(embed=to_send)
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

        # ── Telegram broadcast ─────────────────────────────────────────────────
        tg = getattr(self._bot, "telegram_connector", None)
        if tg and channels_posted:
            tg_text = f"📅 <b>NEW EVENT: {name}</b>\n\n"
            tg_text += f"📅 {date_str} · {time_val}\n"
            tg_text += f"📍 {location}\n"
            if desc:
                tg_text += f"\n{desc[:400]}\n"
            if rsvp_link:
                tg_text += f"\n<a href=\"{rsvp_link}\">Register / RSVP</a>\n"
            tg_text += "\n<i>NJIT Graduate Student Association</i>"
            try:
                await tg.broadcast(tg_text, parse_mode="HTML")
            except Exception as exc:
                logger.warning("Telegram broadcast failed for new event: %s", exc)

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

        # ── Member counts ──────────────────────────────────────────────────────
        guild = interaction.guild
        if guild is not None:
            total_members = guild.member_count or 0
            online_members = sum(
                1 for m in guild.members
                if m.status != discord.Status.offline and not m.bot
            )
            embed.add_field(name="Total Members", value=str(total_members), inline=True)
            embed.add_field(name="Online Now",    value=str(online_members), inline=True)
            embed.add_field(name="​",        value="​",           inline=True)

        embed.add_field(name="Questions",    value=str(stats["total_questions"]),  inline=True)
        embed.add_field(name="Initiatives",  value=str(stats["total_initiatives"]), inline=True)
        embed.add_field(name="Feedback Items", value=str(stats["total_feedback"]), inline=True)

        by_mode = stats.get("questions_by_mode", {})
        gsa  = by_mode.get("gsa",  {"questions": 0, "users": 0})
        free = by_mode.get("free", {"questions": 0, "users": 0})
        embed.add_field(
            name="GSA Mode",
            value=f"{gsa['questions']} questions\n{gsa['users']} users",
            inline=True,
        )
        embed.add_field(
            name="Free Mode",
            value=f"{free['questions']} questions\n{free['users']} users",
            inline=True,
        )
        embed.add_field(name="​", value="​", inline=True)

        if stats["top_topics"]:
            topic_lines = "\n".join(
                f"• {t['matched_topic']} ({t['count']})"
                for t in stats["top_topics"]
            )
            embed.add_field(name="Top Search Topics", value=topic_lines, inline=False)

        # ── RAG stats ──────────────────────────────────────────────────────────
        vector_store = getattr(self.bot, "vector_store", None)
        retriever = getattr(self.bot, "retriever", None)
        conv_manager = getattr(self.bot, "conversation_manager", None)

        rag_status = "✅ Active" if retriever else "❌ Disabled"
        embed.add_field(name="RAG Retriever", value=rag_status, inline=True)

        if vector_store:
            chunk_count = vector_store.get_chunk_count()
            embed.add_field(name="Vector Store Chunks", value=str(chunk_count), inline=True)
        else:
            embed.add_field(name="Vector Store", value="Not initialized", inline=True)

        if conv_manager:
            conv_stats = conv_manager.get_stats()
            embed.add_field(
                name="Active Conversations",
                value=str(conv_stats.get("active_sessions", 0)),
                inline=True,
            )
            timeout = getattr(conv_manager, "timeout_minutes", 60)
            embed.add_field(
                name="Conv. Timeout",
                value=f"{timeout} min",
                inline=True,
            )

        self.bot.db.log_admin_action(  # type: ignore[attr-defined]
            officer_id=interaction.user.id, action="admin_stats", detail=None
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /admin_gaps ────────────────────────────────────────────────────────────

    @app_commands.command(
        name="admin_gaps",
        description="[Admin] Show knowledge-base gaps: unanswered and low-rated questions.",
    )
    @app_commands.describe(days="Look-back window in days (default 30).")
    async def admin_gaps(
        self,
        interaction: discord.Interaction,
        days: int = 30,
    ) -> None:
        if not _admin_check(interaction):
            await interaction.response.send_message(
                NO_PERMISSION.format(role=config.admin_role_name), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        db = self.bot.db  # type: ignore[attr-defined]
        summary = db.get_gaps_summary(days=days)

        total_q   = summary["total_questions"]
        rate      = summary["answered_rate"]
        fb        = summary["feedback_totals"]
        top_gaps  = summary["top_gaps"]
        never     = summary["never_matched_topics"]

        embed = discord.Embed(
            title=f"📉  GSA Knowledge Gaps — Last {days} Days",
            color=NJIT_RED,
        )

        # ── Overview ───────────────────────────────────────────────────────────
        answered_n = round(total_q * rate / 100) if total_q else 0
        embed.add_field(
            name="Coverage",
            value=f"**{rate}%** answered ({answered_n}/{total_q} questions matched well)",
            inline=False,
        )

        sat = fb.get("satisfaction_rate")
        up  = fb.get("thumbs_up", 0)
        dn  = fb.get("thumbs_down", 0)
        rg  = fb.get("regenerate", 0)
        sat_str = f"{sat}%" if sat is not None else "no ratings yet"
        embed.add_field(
            name="Satisfaction",
            value=f"**{sat_str}**  👍 {up}  👎 {dn}  🔄 {rg}",
            inline=False,
        )

        # ── Top gaps ───────────────────────────────────────────────────────────
        if not top_gaps:
            embed.add_field(
                name="Gaps",
                value="✅ No significant gaps detected in the last {days} days.".format(
                    days=days
                ),
                inline=False,
            )
        else:
            lines = []
            for i, g in enumerate(top_gaps[:10], 1):
                q_text  = g["question_text"][:60]
                score   = g["priority_score"]
                asked   = g["times_asked"]
                td      = g["thumbs_down_count"]
                conf    = g["avg_confidence"]
                td_str  = f", {td} 👎" if td else ""
                lines.append(
                    f"**{i}.** [{score:.1f}] \"{q_text}\" — "
                    f"asked {asked}×{td_str}, conf {conf:.0f}%"
                )
            embed.add_field(
                name="Top Gaps to Fix (by priority score)",
                value="\n".join(lines),
                inline=False,
            )

        # ── Never matched ──────────────────────────────────────────────────────
        if never:
            nm_lines = [f"• {q[:70]}" for q in never[:5]]
            embed.add_field(
                name="No KB Match At All",
                value="\n".join(nm_lines),
                inline=False,
            )

        embed.set_footer(
            text=(
                "Priority = (times_asked × 2) + (👎 × 3) + (1 − confidence) × 5  "
                "· Higher = fix first"
            )
        )

        db.log_admin_action(
            officer_id=interaction.user.id,
            action="admin_gaps",
            detail=f"days={days} total_q={total_q} gaps={len(top_gaps)}",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /admin_rebuild_index ───────────────────────────────────────────────────

    @app_commands.command(
        name="admin_rebuild_index",
        description="[Admin] Rebuild the vector index from knowledge base files.",
    )
    async def admin_rebuild_index(self, interaction: discord.Interaction) -> None:
        if not _admin_check(interaction):
            await interaction.response.send_message(
                NO_PERMISSION.format(role=config.admin_role_name), ephemeral=True
            )
            return

        await interaction.response.send_message(
            "🔄 Rebuilding vector index... This may take 2–5 minutes.",
            ephemeral=True,
        )

        vector_store = getattr(self.bot, "vector_store", None)
        embedder = getattr(self.bot, "embedder", None)

        if not vector_store or not embedder:
            await interaction.followup.send(
                "❌ RAG services not initialized. Check Ollama connection.",
                ephemeral=True,
            )
            return

        try:
            from pathlib import Path
            from bot.services.chunker import DocumentChunker

            vector_store.reset()
            data_dir = Path("bot/data")
            chunker = DocumentChunker(data_dir=data_dir)
            chunks = chunker.chunk_all()
            texts = [c.text for c in chunks]
            embeddings = await embedder.embed_batch(texts, batch_size=10)
            vector_store.add_chunks(chunks, embeddings)

            retriever = getattr(self.bot, "retriever", None)
            if retriever:
                retriever.rebuild_bm25_index()

            success_count = sum(1 for e in embeddings if e is not None)
            await interaction.followup.send(
                f"✅ Index rebuilt: **{success_count}** chunks indexed "
                f"({len(chunks) - success_count} failed).",
                ephemeral=True,
            )
            logger.info(
                "Admin %s rebuilt vector index: %d chunks",
                interaction.user.id,
                success_count,
            )
        except Exception as exc:
            logger.error("admin_rebuild_index error: %s", exc, exc_info=True)
            await interaction.followup.send(
                f"❌ Index rebuild failed: {exc}",
                ephemeral=True,
            )

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

        tg = getattr(self.bot, "telegram_connector", None)
        if tg:
            try:
                await tg.broadcast(message.strip())
            except Exception as exc:
                logger.warning("Telegram broadcast failed for admin_announce: %s", exc)

        self.bot.db.log_admin_action(  # type: ignore[attr-defined]
            officer_id=interaction.user.id,
            action="admin_announce",
            detail=f"channel={channel.name}",
        )
        await interaction.response.send_message(
            f"✅ Announcement posted in {channel.mention} and Telegram channel.", ephemeral=True
        )

    # ── /admin_announce_event ─────────────────────────────────────────────────

    @app_commands.command(
        name="admin_announce_event",
        description="[Admin] Manually post a formatted event announcement from the KB.",
    )
    @app_commands.describe(
        event_name="Name of the event (partial names work).",
        channel="Channel to post in (defaults to #gsa-announcements).",
    )
    async def admin_announce_event(
        self,
        interaction: discord.Interaction,
        event_name: str,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        if not _admin_check(interaction):
            await interaction.response.send_message(
                NO_PERMISSION.format(role=config.admin_role_name), ephemeral=True
            )
            return

        from bot.services.announcements import format_event_announcement
        from bot.services.channels import get_announcement_channel

        # Fuzzy-match the event from the KB
        matches = self.bot.search_svc.search_events(event_name)  # type: ignore[attr-defined]
        if not matches:
            await interaction.response.send_message(
                f"No event found matching **{event_name}**. Use `/events` to see available events.",
                ephemeral=True,
            )
            return

        event, _ = matches[0]
        event_dict = {
            "name": event.name,
            "date": event.date,
            "time": event.time,
            "location": event.location,
            "description": event.description,
            "organizer": event.organizer,
            "rsvp_link": event.rsvp_link,
            "category": event.category,
        }

        target = channel or get_announcement_channel(interaction.guild)
        if target is None:
            await interaction.response.send_message(
                "No announcement channel found. Pass a channel or create #gsa-announcements.",
                ephemeral=True,
            )
            return

        embed = format_event_announcement(event_dict, "new")
        try:
            await target.send(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"I don't have permission to post in {target.mention}.", ephemeral=True
            )
            return

        tg = getattr(self.bot, "telegram_connector", None)
        if tg:
            tg_text = f"📅 <b>NEW EVENT: {event.name}</b>\n\n"
            tg_text += f"📅 {event.date} · {event.time}\n"
            tg_text += f"📍 {event.location}\n"
            if event.description:
                tg_text += f"\n{event.description[:400]}\n"
            if event.rsvp_link:
                tg_text += f"\n<a href=\"{event.rsvp_link}\">Register / RSVP</a>\n"
            tg_text += "\n<i>NJIT Graduate Student Association</i>"
            try:
                await tg.broadcast(tg_text, parse_mode="HTML")
            except Exception as exc:
                logger.warning("Telegram broadcast failed for announce_event: %s", exc)

        self.bot.db.log_admin_action(  # type: ignore[attr-defined]
            officer_id=interaction.user.id,
            action="admin_announce_event",
            detail=f"event={event.name}, channel={target.name}",
        )
        await interaction.response.send_message(
            f"✅ Announcement for **{event.name}** posted in {target.mention}.", ephemeral=True
        )

    # ── /admin_broadcast ──────────────────────────────────────────────────────

    @app_commands.command(
        name="admin_broadcast",
        description="[Admin] Broadcast a message to a Discord channel and Telegram channel.",
    )
    @app_commands.describe(
        message="The message to broadcast.",
        channel="Discord channel name to post in (default: gsa-announcements).",
    )
    async def admin_broadcast(
        self,
        interaction: discord.Interaction,
        message: str,
        channel: str = "gsa-announcements",
    ) -> None:
        if not _admin_check(interaction):
            await interaction.response.send_message(
                NO_PERMISSION.format(role=config.admin_role_name), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        results: list[str] = []

        # ── Discord ───────────────────────────────────────────────────────────
        guild = interaction.guild
        discord_channel = discord.utils.get(guild.text_channels, name=channel) if guild else None
        if discord_channel:
            try:
                await discord_channel.send(message)
                results.append(f"✅ Discord #{channel}")
            except discord.Forbidden:
                results.append(f"❌ Discord #{channel} — missing permission")
        else:
            results.append(f"❌ Discord #{channel} not found")

        # ── Telegram ──────────────────────────────────────────────────────────
        tg = getattr(self.bot, "telegram_connector", None)
        if tg:
            success = await tg.broadcast(message)
            if success:
                results.append("✅ Telegram channel")
            else:
                results.append("❌ Telegram channel (check logs)")
        else:
            results.append("⚠️ Telegram not configured")

        self.bot.db.log_admin_action(  # type: ignore[attr-defined]
            officer_id=interaction.user.id,
            action="admin_broadcast",
            detail=f"channel={channel}",
        )
        await interaction.followup.send(
            "Broadcast results:\n" + "\n".join(results),
            ephemeral=True,
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


    # ── /mathcafe_add ──────────────────────────────────────────────────────────

    @app_commands.command(
        name="mathcafe_add",
        description="[Admin] Add a new MathCafe fact or puzzle to the queue.",
    )
    async def mathcafe_add(self, interaction: discord.Interaction) -> None:
        if not _admin_check(interaction):
            await interaction.response.send_message(
                NO_PERMISSION.format(role=config.admin_role_name), ephemeral=True
            )
            return
        modal = MathCafeAddModal(bot=self.bot)
        await interaction.response.send_modal(modal)

    # ── /mathcafe_preview ──────────────────────────────────────────────────────

    @app_commands.command(
        name="mathcafe_preview",
        description="[Admin] Preview the next MathCafe fact without posting it.",
    )
    async def mathcafe_preview(self, interaction: discord.Interaction) -> None:
        if not _admin_check(interaction):
            await interaction.response.send_message(
                NO_PERMISSION.format(role=config.admin_role_name), ephemeral=True
            )
            return

        mathcafe = getattr(self.bot, "mathcafe", None)
        if mathcafe is None:
            await interaction.response.send_message(
                "MathCafe service not initialized.", ephemeral=True
            )
            return

        next_fact = mathcafe.get_next_fact()
        if not next_fact:
            await interaction.response.send_message(
                "No facts in queue.", ephemeral=True
            )
            return

        embed = mathcafe.build_embed(next_fact, datetime.date.today())
        embed.title = "👀 Preview (not posted yet)"
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /mathcafe_post_now ─────────────────────────────────────────────────────

    @app_commands.command(
        name="mathcafe_post_now",
        description="[Admin] Post today's MathCafe fact immediately (for testing).",
    )
    async def mathcafe_post_now(self, interaction: discord.Interaction) -> None:
        if not _admin_check(interaction):
            await interaction.response.send_message(
                NO_PERMISSION.format(role=config.admin_role_name), ephemeral=True
            )
            return

        mathcafe = getattr(self.bot, "mathcafe", None)
        if mathcafe is None:
            await interaction.response.send_message(
                "MathCafe service not initialized.", ephemeral=True
            )
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command must be used inside a server.", ephemeral=True
            )
            return

        channel = discord.utils.get(guild.text_channels, name=config.mathcafe_channel)
        if channel is None:
            await interaction.response.send_message(
                f"Channel `#{config.mathcafe_channel}` not found. "
                f"Create it in Discord first.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        success = await mathcafe.post_fact(channel)

        if success:
            tg = getattr(self.bot, "telegram_connector", None)
            fact = getattr(mathcafe, "last_posted_fact", None)
            if tg and fact:
                title  = fact.get("title", "")
                body   = fact.get("body", "")
                footer = fact.get("footer", "GSA MathCafe")
                text   = f"☕ <b>GSA MathCafe</b>\n\n<b>{title}</b>\n\n{body}"
                if fact.get("has_spoiler") and fact.get("spoiler_text"):
                    text += f"\n\n<tg-spoiler>{fact['spoiler_text']}</tg-spoiler>"
                text += f"\n\n<i>{footer}</i>"
                if fact.get("needs_image") and fact.get("image_filename"):
                    image_path = f"bot/data/mathcafe/images/{fact['image_filename']}"
                    await tg.broadcast_photo(photo_path=image_path, caption=text, parse_mode="HTML")
                else:
                    await tg.broadcast(text, parse_mode="HTML")
            await interaction.followup.send("✅ MathCafe fact posted to Discord and Telegram!", ephemeral=True)
        else:
            await interaction.followup.send(
                "❌ Failed — no facts available.", ephemeral=True
            )


# ── MathCafe add modal ─────────────────────────────────────────────────────────

class MathCafeAddModal(discord.ui.Modal, title="Add MathCafe Fact"):
    fact_title = discord.ui.TextInput(
        label="Title",
        placeholder="The Monty Hall Problem",
        max_length=100,
        required=True,
    )
    body = discord.ui.TextInput(
        label="Body",
        style=discord.TextStyle.paragraph,
        placeholder="Describe the fact or puzzle here...",
        max_length=1500,
        required=True,
    )
    category = discord.ui.TextInput(
        label="Category (math / cs / history / science)",
        placeholder="math",
        max_length=20,
        required=True,
    )
    discussion = discord.ui.TextInput(
        label="Enable discussion thread? (yes / no)",
        placeholder="no",
        max_length=3,
        required=False,
    )
    day_preference = discord.ui.TextInput(
        label="Day preference (monday–friday / any)",
        placeholder="any",
        max_length=10,
        required=False,
    )

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self._bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        mathcafe = getattr(self._bot, "mathcafe", None)
        if mathcafe is None:
            await interaction.followup.send(
                "MathCafe service not initialized.", ephemeral=True
            )
            return

        discussion_flag = self.discussion.value.strip().lower() == "yes"
        day_pref = self.day_preference.value.strip().lower() or "any"

        new_fact = await mathcafe.add_fact(
            title=self.fact_title.value.strip(),
            body=self.body.value.strip(),
            category=self.category.value.strip().lower(),
            discussion=discussion_flag,
            day_preference=day_pref,
        )

        await interaction.followup.send(
            f"✅ MathCafe fact added!\n"
            f"**ID:** {new_fact['id']}\n"
            f"**Title:** {new_fact['title']}\n"
            f"**Category:** {new_fact['category']}\n"
            f"**Total facts in queue:** {len(mathcafe.facts)}\n\n"
            f"It will be posted at 9 AM NJ time on the next available day.",
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error("MathCafeAddModal error: %s", error, exc_info=error)
        try:
            await interaction.followup.send(
                "❌ Something went wrong adding the fact. Please try again.",
                ephemeral=True,
            )
        except Exception:
            pass


async def setup(bot: commands.Bot) -> None:
    """Extension entry point."""
    await bot.add_cog(AdminCog(bot))
