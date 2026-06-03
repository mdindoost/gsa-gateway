"""Admin slash commands — all ephemeral, gated behind ADMIN_ROLE_NAME."""

import csv
import datetime
import io
import logging
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
            await interaction.followup.send("✅ MathCafe fact posted now!", ephemeral=True)
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
