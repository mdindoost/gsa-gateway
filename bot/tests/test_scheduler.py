"""Tests for scheduler helpers, channel routing, and announcement embeds."""

from datetime import date, timedelta, datetime, timezone, time
from unittest.mock import MagicMock

import discord
import pytest

from bot.services.announcements import format_event_announcement
from bot.services.knowledge_base import Event
from bot.services.scheduler import _is_upcoming, _parse_start_time


# ── _parse_start_time ─────────────────────────────────────────────────────────

class TestParseStartTime:
    def test_12h_with_minutes_pm(self):
        dt = _parse_start_time("2026-06-06", "4:00 PM")
        assert dt is not None and dt.hour == 16 and dt.minute == 0

    def test_12h_with_minutes_am(self):
        dt = _parse_start_time("2026-06-06", "9:30 AM")
        assert dt is not None and dt.hour == 9 and dt.minute == 30

    def test_12h_without_minutes(self):
        dt = _parse_start_time("2026-06-06", "4 PM")
        assert dt is not None and dt.hour == 16 and dt.minute == 0

    def test_noon_12pm(self):
        dt = _parse_start_time("2026-06-06", "12:00 PM")
        assert dt is not None and dt.hour == 12

    def test_midnight_12am(self):
        dt = _parse_start_time("2026-06-06", "12:00 AM")
        assert dt is not None and dt.hour == 0

    def test_range_extracts_start_time(self):
        dt = _parse_start_time("2026-06-06", "4:00 PM - 7:00 PM")
        assert dt is not None and dt.hour == 16

    def test_tbd_returns_noon(self):
        dt = _parse_start_time("2026-06-06", "TBD")
        assert dt is not None and dt.hour == 12

    def test_empty_string_returns_noon(self):
        dt = _parse_start_time("2026-06-06", "")
        assert dt is not None and dt.hour == 12

    def test_invalid_date_returns_none(self):
        dt = _parse_start_time("not-a-date", "4 PM")
        assert dt is None

    def test_result_is_utc_aware(self):
        dt = _parse_start_time("2026-06-06", "3:00 PM")
        assert dt is not None and dt.tzinfo == timezone.utc


# ── _is_upcoming ──────────────────────────────────────────────────────────────

class TestIsUpcoming:
    def _today(self):
        return date.today()

    def test_today_is_upcoming(self):
        today = self._today()
        assert _is_upcoming(str(today), today, today + timedelta(days=7))

    def test_cutoff_day_is_upcoming(self):
        today = self._today()
        cutoff = today + timedelta(days=7)
        assert _is_upcoming(str(cutoff), today, cutoff)

    def test_yesterday_not_upcoming(self):
        today = self._today()
        yesterday = today - timedelta(days=1)
        assert not _is_upcoming(str(yesterday), today, today + timedelta(days=7))

    def test_8_days_out_not_upcoming(self):
        today = self._today()
        far = today + timedelta(days=8)
        assert not _is_upcoming(str(far), today, today + timedelta(days=7))

    def test_invalid_date_returns_false(self):
        today = self._today()
        assert not _is_upcoming("not-a-date", today, today + timedelta(days=7))


# ── DB reminder logic ─────────────────────────────────────────────────────────

class TestReminderDatabaseLogic:
    def test_event_returned_for_future_date(self, db):
        future = (date.today() + timedelta(days=7)).isoformat()
        db.add_event("Event A", future, "3 PM", "Campus", "desc", "GSA", "", "events", 999)
        events = db.get_events_for_reminders()
        assert any(e["name"] == "Event A" for e in events)

    def test_past_event_not_returned(self, db):
        past = (date.today() - timedelta(days=1)).isoformat()
        db.add_event("Past Event", past, "3 PM", "Campus", "desc", "GSA", "", "events", 999)
        events = db.get_events_for_reminders()
        assert all(e["name"] != "Past Event" for e in events)

    def test_reminder_sent_7d_flag_starts_false(self, db):
        future = (date.today() + timedelta(days=7)).isoformat()
        db.add_event("Event B", future, "3 PM", "Campus", "desc", "GSA", "", "events", 999)
        events = db.get_events_for_reminders()
        target = next(e for e in events if e["name"] == "Event B")
        assert not target["reminder_sent_7d"]

    def test_mark_reminder_sent_7d_sets_flag(self, db):
        future = (date.today() + timedelta(days=7)).isoformat()
        eid = db.add_event("Event C", future, "3 PM", "Campus", "desc", "GSA", "", "events", 999)
        db.mark_reminder_sent(eid, "7d")
        events = db.get_events_for_reminders()
        target = next(e for e in events if e["name"] == "Event C")
        assert target["reminder_sent_7d"]

    def test_mark_reminder_sent_1d_sets_flag(self, db):
        future = (date.today() + timedelta(days=1)).isoformat()
        eid = db.add_event("Event D", future, "3 PM", "Campus", "desc", "GSA", "", "events", 999)
        db.mark_reminder_sent(eid, "1d")
        events = db.get_events_for_reminders()
        target = next(e for e in events if e["name"] == "Event D")
        assert target["reminder_sent_1d"]

    def test_duplicate_7d_reminder_blocked_by_flag(self, db):
        future = (date.today() + timedelta(days=7)).isoformat()
        eid = db.add_event("Event E", future, "3 PM", "Campus", "desc", "GSA", "", "events", 999)
        db.mark_reminder_sent(eid, "7d")
        events = db.get_events_for_reminders()
        target = next(e for e in events if e["name"] == "Event E")
        # Flag is True — scheduler will skip this event
        assert bool(target["reminder_sent_7d"])

    def test_get_upcoming_events_db_range(self, db):
        today = date.today().isoformat()
        future_3 = (date.today() + timedelta(days=3)).isoformat()
        future_10 = (date.today() + timedelta(days=10)).isoformat()
        db.add_event("Today",   today,     "3 PM", "Campus", "", "GSA", "", "events", 999)
        db.add_event("3 days",  future_3,  "3 PM", "Campus", "", "GSA", "", "events", 999)
        db.add_event("10 days", future_10, "3 PM", "Campus", "", "GSA", "", "events", 999)
        upcoming = db.get_upcoming_events_db(days=7)
        names = [e["name"] for e in upcoming]
        assert "Today"   in names
        assert "3 days"  in names
        assert "10 days" not in names


# ── Channel routing ───────────────────────────────────────────────────────────

class TestChannelRouting:
    def _make_guild(self, *channel_names: str) -> MagicMock:
        guild = MagicMock(spec=discord.Guild)
        guild.name = "Test Guild"
        channels = []
        for name in channel_names:
            ch = MagicMock(spec=discord.TextChannel)
            ch.name = name
            ch.id = hash(name)
            channels.append(ch)
        guild.text_channels = channels
        return guild

    def test_food_category_routes_to_food_channel(self):
        from bot.config import config
        from bot.services.channels import get_channel_for_category
        guild = self._make_guild(config.channel_food, config.channel_announcements)
        ch = get_channel_for_category(guild, "food")
        assert ch is not None
        assert ch.name == config.channel_food

    def test_academic_routes_to_events_channel(self):
        from bot.config import config
        from bot.services.channels import get_channel_for_category
        guild = self._make_guild(config.channel_events, config.channel_announcements)
        ch = get_channel_for_category(guild, "academic")
        assert ch is not None
        assert ch.name == config.channel_events

    def test_missing_specific_channel_falls_back_to_announcements(self):
        from bot.config import config
        from bot.services.channels import get_channel_for_category
        # Only announcements exists, not the food channel
        guild = self._make_guild(config.channel_announcements)
        ch = get_channel_for_category(guild, "food")
        assert ch is not None
        assert ch.name == config.channel_announcements

    def test_both_channels_missing_returns_none(self):
        from bot.services.channels import get_channel_for_category
        guild = self._make_guild()  # no channels at all
        ch = get_channel_for_category(guild, "food")
        assert ch is None

    def test_get_channels_for_categories_deduplicates(self):
        from bot.config import config
        from bot.services.channels import get_channels_for_categories
        # Both "social" and "academic" map to channel_events — should appear once
        guild = self._make_guild(config.channel_events, config.channel_announcements)
        channels = get_channels_for_categories(guild, ["social", "academic"])
        names = [ch.name for ch in channels]
        assert names.count(config.channel_events) == 1

    def test_get_announcement_channel(self):
        from bot.config import config
        from bot.services.channels import get_announcement_channel
        guild = self._make_guild(config.channel_announcements)
        ch = get_announcement_channel(guild)
        assert ch is not None
        assert ch.name == config.channel_announcements


# ── Announcement embeds ───────────────────────────────────────────────────────

class TestAnnouncementFormatter:
    def _event_dict(self):
        return {
            "name":        "Test Happy Hour",
            "date":        "2026-06-06",
            "time":        "4:00 PM - 7:00 PM",
            "location":    "Highlander Pub",
            "description": "Weekly happy hour for grad students.",
            "organizer":   "GSA",
            "rsvp_link":   "https://instagram.com/njit.gsa",
            "category":    "food",
        }

    def test_new_event_color_is_green(self):
        embed = format_event_announcement(self._event_dict(), "new")
        assert embed.color == discord.Color.from_str("#00AA00")

    def test_new_event_title_contains_name(self):
        embed = format_event_announcement(self._event_dict(), "new")
        assert "Test Happy Hour" in embed.title
        assert "NEW EVENT" in embed.title

    def test_7d_reminder_color_is_blue(self):
        embed = format_event_announcement(self._event_dict(), "reminder_7d")
        assert embed.color == discord.Color.from_str("#0055AA")

    def test_7d_reminder_title_contains_coming_next_week(self):
        embed = format_event_announcement(self._event_dict(), "reminder_7d")
        assert "Coming Next Week" in embed.title

    def test_1d_reminder_color_is_orange(self):
        embed = format_event_announcement(self._event_dict(), "reminder_1d")
        assert embed.color == discord.Color.from_str("#FF8800")

    def test_1d_reminder_title_contains_tomorrow(self):
        embed = format_event_announcement(self._event_dict(), "reminder_1d")
        assert "Tomorrow" in embed.title

    def test_1h_reminder_color_is_red(self):
        embed = format_event_announcement(self._event_dict(), "reminder_1h")
        assert embed.color == discord.Color.from_str("#CC0000")

    def test_1h_reminder_title_contains_starting_soon(self):
        embed = format_event_announcement(self._event_dict(), "reminder_1h")
        assert "Starting Soon" in embed.title

    def test_works_with_event_dataclass(self):
        ev = Event(
            name="Research Day", date="2026-11-25", time="9:00 AM",
            location="Ballroom", description="Annual research showcase.",
            organizer="GSA VP", rsvp_link="", category="academic",
        )
        embed = format_event_announcement(ev, "new")
        assert "Research Day" in embed.title
        assert embed.color == discord.Color.from_str("#00AA00")

    def test_footer_text_correct_for_each_type(self):
        ev = self._event_dict()
        assert "Add to your calendar" in format_event_announcement(ev, "new").footer.text
        assert "7 days away"          in format_event_announcement(ev, "reminder_7d").footer.text
        assert "Don't forget"         in format_event_announcement(ev, "reminder_1d").footer.text
        assert "Starting in 1 hour"   in format_event_announcement(ev, "reminder_1h").footer.text

    def test_unknown_type_returns_embed_with_name(self):
        embed = format_event_announcement(self._event_dict(), "unknown_type")
        assert "Test Happy Hour" in embed.title
