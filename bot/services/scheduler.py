"""Background scheduled tasks: event reminders and daily digest.

Two tasks:
  check_upcoming_reminders — runs every REMINDER_CHECK_INTERVAL minutes (default 30)
  daily_digest             — runs daily at DAILY_DIGEST_HOUR:DAILY_DIGEST_MINUTE UTC
"""

import datetime
import logging
import re
import zoneinfo
from typing import Optional

import discord
from discord.ext import commands, tasks

from bot.config import config
from bot.services.announcements import format_event_announcement
from bot.services.channels import get_announcement_channel, get_channel_for_category

logger = logging.getLogger(__name__)

# Module-level constants used in task decorators (evaluated at import time)
_DIGEST_TIME = datetime.time(
    hour=config.daily_digest_hour,
    minute=config.daily_digest_minute,
    tzinfo=datetime.timezone.utc,
)
_REMINDER_INTERVAL = config.reminder_check_interval
_MATHCAFE_TIME = datetime.time(
    hour=9,
    minute=0,
    tzinfo=zoneinfo.ZoneInfo("America/New_York"),
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_start_time(date_str: str, time_str: str) -> Optional[datetime.datetime]:
    """Parse an event date + time string into a UTC-aware datetime.

    Returns noon UTC on the event date when time is 'TBD' or unparseable.
    """
    try:
        base = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None

    if not time_str or time_str.upper() == "TBD":
        return base.replace(hour=12, tzinfo=datetime.timezone.utc)

    # Try progressively simpler patterns; first match wins
    patterns = [
        (r"(\d{1,2}):(\d{2})\s*(AM|PM)", "hm_ampm"),
        (r"(\d{1,2})\s*(AM|PM)",          "h_ampm"),
        (r"(\d{2}):(\d{2})",              "hm_24"),
    ]
    for pattern, kind in patterns:
        m = re.search(pattern, time_str, re.IGNORECASE)
        if not m:
            continue
        try:
            if kind == "hm_ampm":
                hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3).upper()
                if ampm == "PM" and hour != 12:
                    hour += 12
                elif ampm == "AM" and hour == 12:
                    hour = 0
            elif kind == "h_ampm":
                hour, ampm = int(m.group(1)), m.group(2).upper()
                minute = 0
                if ampm == "PM" and hour != 12:
                    hour += 12
                elif ampm == "AM" and hour == 12:
                    hour = 0
            else:  # hm_24
                hour, minute = int(m.group(1)), int(m.group(2))
            return base.replace(hour=hour, minute=minute, tzinfo=datetime.timezone.utc)
        except (ValueError, IndexError):
            continue

    return base.replace(hour=12, tzinfo=datetime.timezone.utc)


def _is_upcoming(
    date_str: str,
    today: datetime.date,
    cutoff: datetime.date,
) -> bool:
    """Return True if date_str falls in [today, cutoff]."""
    try:
        d = datetime.date.fromisoformat(str(date_str))
        return today <= d <= cutoff
    except ValueError:
        return False


# ── Cog ───────────────────────────────────────────────────────────────────────

class SchedulerCog(commands.Cog, name="Scheduler"):
    """Background task runner for reminders and daily digest."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.mathcafe = getattr(bot, "mathcafe", None)
        self.check_upcoming_reminders.start()
        self.daily_digest.start()
        self.post_mathcafe_daily.start()

    def cog_unload(self) -> None:
        self.check_upcoming_reminders.cancel()
        self.daily_digest.cancel()
        self.post_mathcafe_daily.cancel()

    def _get_guild(self) -> Optional[discord.Guild]:
        if config.discord_guild_id:
            return self.bot.get_guild(config.discord_guild_id)
        guilds = self.bot.guilds
        return guilds[0] if guilds else None

    # ── Reminder task ──────────────────────────────────────────────────────────

    @tasks.loop(minutes=_REMINDER_INTERVAL)
    async def check_upcoming_reminders(self) -> None:
        """Send 7-day, 1-day, and 1-hour reminders for upcoming events."""
        db = getattr(self.bot, "db", None)
        if db is None:
            return

        guild = self._get_guild()
        if guild is None:
            logger.warning("Scheduler: no guild found — reminders skipped")
            return

        now   = datetime.datetime.now(datetime.timezone.utc)
        today = now.date()

        try:
            events = db.get_events_for_reminders()
        except Exception as exc:
            logger.error("Scheduler: DB error fetching events: %s", exc)
            return

        for event in events:
            try:
                await self._process_reminders(guild, event, now, today)
            except Exception as exc:
                logger.error(
                    "Scheduler: error processing reminder for '%s': %s",
                    event.get("name"), exc,
                )

    async def _process_reminders(
        self,
        guild: discord.Guild,
        event: dict,
        now: datetime.datetime,
        today: datetime.date,
    ) -> None:
        db = self.bot.db  # type: ignore[attr-defined]
        try:
            event_date = datetime.date.fromisoformat(event["date"])
        except (ValueError, KeyError):
            return

        delta = (event_date - today).days

        if delta == 7 and not event["reminder_sent_7d"]:
            await self._send_reminder(guild, event, "reminder_7d", db)
            db.mark_reminder_sent(event["id"], "7d")

        elif delta == 1 and not event["reminder_sent_1d"]:
            await self._send_reminder(guild, event, "reminder_1d", db)
            db.mark_reminder_sent(event["id"], "1d")

        elif delta == 0 and not event["reminder_sent_1h"]:
            event_dt = _parse_start_time(event["date"], event.get("time", "TBD"))
            if event_dt and 0 <= (event_dt - now).total_seconds() <= 3600:
                await self._send_reminder(guild, event, "reminder_1h", db)
                db.mark_reminder_sent(event["id"], "1h")

    async def _send_reminder(
        self,
        guild: discord.Guild,
        event: dict,
        reminder_type: str,
        db,
    ) -> None:
        embed = format_event_announcement(event, reminder_type)
        ch = get_channel_for_category(guild, event.get("category", "general"))
        if ch is None:
            logger.warning(
                "Scheduler: no channel for category '%s', skipping reminder",
                event.get("category"),
            )
            return
        try:
            await ch.send(embed=embed)
            logger.info(
                "Sent %s reminder for '%s' in #%s",
                reminder_type, event["name"], ch.name,
            )
        except discord.Forbidden:
            logger.warning(
                "Scheduler: missing permission to post in #%s", ch.name
            )

    @check_upcoming_reminders.before_loop
    async def _before_reminders(self) -> None:
        await self.bot.wait_until_ready()

    # ── Daily digest task ──────────────────────────────────────────────────────

    @tasks.loop(time=_DIGEST_TIME)
    async def daily_digest(self) -> None:
        """Post a morning digest to #gsa-announcements when events are this week."""
        guild = self._get_guild()
        if guild is None:
            return

        ch = get_announcement_channel(guild)
        if ch is None:
            logger.warning("Scheduler: announcements channel missing, skipping digest")
            return

        today  = datetime.date.today()
        cutoff = today + datetime.timedelta(days=7)

        # Collect events from the KB (YAML) and DB
        upcoming: list[tuple[str, str]] = []  # (name, date)

        kb = getattr(self.bot, "kb", None)
        if kb is not None:
            for ev in kb.events:
                if _is_upcoming(ev.date, today, cutoff):
                    upcoming.append((ev.name, ev.date))

        db = getattr(self.bot, "db", None)
        if db is not None:
            try:
                kb_names = {name for name, _ in upcoming}
                for ev in db.get_upcoming_events_db(days=7):
                    if ev["name"] not in kb_names:
                        upcoming.append((ev["name"], ev["date"]))
            except Exception as exc:
                logger.error("Scheduler: DB error during digest: %s", exc)

        if not upcoming:
            return

        upcoming.sort(key=lambda t: t[1])
        lines = [f"• **{name}** — {date}" for name, date in upcoming]

        embed = discord.Embed(
            title="📅 This Week from GSA",
            description="\n".join(lines),
            color=discord.Color.from_str("#0055AA"),
        )
        embed.set_footer(text="Check #gsa-events for details • GSA Gateway")

        try:
            await ch.send(embed=embed)
            logger.info("Daily digest posted: %d events", len(upcoming))
        except discord.Forbidden:
            logger.warning(
                "Scheduler: missing permission to post digest in #%s", ch.name
            )

    @daily_digest.before_loop
    async def _before_digest(self) -> None:
        await self.bot.wait_until_ready()

    # ── MathCafe daily post task ───────────────────────────────────────────────

    @tasks.loop(time=_MATHCAFE_TIME)
    async def post_mathcafe_daily(self) -> None:
        """Post the next MathCafe fact to #gsa-mathcafe at 9 AM NJ time."""
        mathcafe = getattr(self.bot, "mathcafe", None)
        if mathcafe is None:
            logger.warning("MathCafe service not initialized — skipping daily post")
            return

        guild = self._get_guild()
        if guild is None:
            logger.warning("Guild not found for MathCafe post")
            return

        channel = discord.utils.get(
            guild.text_channels,
            name=config.mathcafe_channel,
        )
        if channel is None:
            logger.warning(
                "MathCafe channel '%s' not found in Discord server. "
                "Create a channel named exactly '%s' to enable daily posts.",
                config.mathcafe_channel,
                config.mathcafe_channel,
            )
            return

        success = await mathcafe.post_fact(channel)
        if success:
            logger.info("MathCafe daily post completed")
        else:
            logger.warning("MathCafe daily post failed — no facts available")

    @post_mathcafe_daily.before_loop
    async def _before_mathcafe(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SchedulerCog(bot))
