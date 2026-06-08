"""Background scheduled tasks: event reminders and daily digest.

Two tasks:
  check_upcoming_reminders — runs every REMINDER_CHECK_INTERVAL minutes (default 30)
  daily_digest             — runs daily at DAILY_DIGEST_HOUR:DAILY_DIGEST_MINUTE UTC
"""

import asyncio
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
_WC_SCHEDULE_TIME = datetime.time(
    hour=8, minute=0,
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


async def _broadcast_wc_event(bot, event: dict, tracker) -> None:
    """Broadcast a World Cup event to Telegram."""
    tg = getattr(bot, "telegram_connector", None)
    if not tg:
        return

    from bot.services.worldcup_embeds import format_score, format_stage, format_group

    etype = event["type"]
    m     = event["match"]
    home  = tracker.format_team_name(m["homeTeam"])
    away  = tracker.format_team_name(m["awayTeam"])
    stage = format_stage(m.get("stage", ""))
    group = format_group(m.get("group", "") or "")
    matchday = m.get("matchday")

    def _context_line() -> str:
        parts = [f"🏆 {stage}"]
        if group and group != "Knockout Stage":
            parts.append(group)
        if matchday:
            parts.append(f"Matchday {matchday}")
        return " · ".join(parts)

    if etype == "kickoff":
        referees = m.get("referees", [])
        text = (
            f"⚽ <b>MATCH STARTING NOW!</b>\n\n"
            f"<b>{home}  vs  {away}</b>\n\n"
            f"{_context_line()}\n"
        )
        if referees:
            ref     = referees[0]
            ref_nat = ref.get("nationality", "")
            ref_str = ref.get("name", "")
            if ref_nat:
                ref_str += f" ({ref_nat})"
            text += f"👨‍⚖️ {ref_str}\n"
        text += "\n<i>FIFA World Cup 2026 · GSA Gateway</i>"

    elif etype == "goal":
        score        = format_score(m)
        scoring_team = event.get("scoring_team")
        team_name    = event.get("team", "")
        scorer_name  = event.get("scorer", "")
        minute       = event.get("minute")

        if scoring_team:
            goal_line = tracker.format_team_name(scoring_team)
        elif team_name:
            goal_line = team_name
        else:
            goal_line = None

        if scorer_name and minute:
            goal_detail = f" · {scorer_name} ({minute}')"
        elif scorer_name:
            goal_detail = f" · {scorer_name}"
        elif minute:
            goal_detail = f" · {minute}'"
        else:
            goal_detail = ""

        body = f"⚽ {goal_line}{goal_detail}\n\n" if goal_line else ""
        text = (
            f"⚽ <b>GOOOOOAL!</b>\n\n"
            f"<b>{home}  {score}  {away}</b>\n\n"
            f"{body}"
            f"{_context_line()}\n\n"
            f"<i>FIFA World Cup 2026 · GSA Gateway</i>"
        )

    elif etype == "halftime":
        ht   = m["score"]["halfTime"]
        ht_h = ht.get("home", 0) or 0
        ht_a = ht.get("away", 0) or 0
        text = (
            f"⏸️ <b>HALF TIME</b>\n\n"
            f"<b>{home}  {ht_h} — {ht_a}  {away}</b>\n\n"
            f"{_context_line()}\n\n"
            f"<i>FIFA World Cup 2026 · GSA Gateway</i>"
        )

    elif etype == "second_half":
        ht   = m["score"]["halfTime"]
        ht_h = ht.get("home", 0) or 0
        ht_a = ht.get("away", 0) or 0
        text = (
            f"▶️ <b>SECOND HALF UNDERWAY</b>\n\n"
            f"<b>{home}  {ht_h} — {ht_a}  {away}</b>\n\n"
            f"{_context_line()}\n\n"
            f"<i>FIFA World Cup 2026 · GSA Gateway</i>"
        )

    elif etype == "fulltime":
        full_score = event.get("full_score") or {}
        ft_home    = m["score"]["fullTime"]["home"] or 0
        ft_away    = m["score"]["fullTime"]["away"] or 0
        score_str  = f"{ft_home} — {ft_away}"
        duration   = full_score.get("duration") or m["score"].get("duration", "REGULAR")

        winner_code = full_score.get("winner") or m["score"].get("winner")
        if winner_code == "HOME_TEAM":
            result = f"🎉 {home} wins!"
        elif winner_code == "AWAY_TEAM":
            result = f"🎉 {away} wins!"
        elif winner_code == "DRAW":
            result = "🤝 It's a draw!"
        else:
            if ft_home > ft_away:
                result = f"🎉 {home} wins!"
            elif ft_away > ft_home:
                result = f"🎉 {away} wins!"
            else:
                result = "🤝 It's a draw!"

        if m.get("stage") == "FINAL":
            text = (
                f"🏆 <b>WORLD CUP CHAMPIONS!</b>\n\n"
                f"<b>{result}</b>\n\n"
                f"2026 FIFA World Cup Champions! 🥇\n\n"
                f"<i>FIFA World Cup 2026 · GSA Gateway</i>"
            )
        else:
            breakdown = ""
            suffix    = ""
            if duration == "PENALTY_SHOOTOUT":
                reg  = full_score.get("regularTime") or {}
                ext  = full_score.get("extraTime") or {}
                pens = full_score.get("penalties") or {}
                r_h, r_a = reg.get("home", 0) or 0, reg.get("away", 0) or 0
                e_h, e_a = ext.get("home", 0) or 0, ext.get("away", 0) or 0
                p_h, p_a = pens.get("home", 0) or 0, pens.get("away", 0) or 0
                breakdown = (
                    f"\n⏱️ 90 min: {r_h} — {r_a}"
                    f"\n⏱️ AET: {r_h + e_h} — {r_a + e_a}"
                    f"\n🥅 Pens: {p_h} — {p_a}"
                )
                suffix = " (on penalties)"
            elif duration == "EXTRA_TIME":
                reg  = full_score.get("regularTime") or {}
                r_h, r_a = reg.get("home", 0) or 0, reg.get("away", 0) or 0
                breakdown = (
                    f"\n⏱️ 90 min: {r_h} — {r_a}"
                    f"\n⏱️ AET: {ft_home} — {ft_away}"
                )
                suffix = " (AET)"

            text = (
                f"🏁 <b>FULL TIME</b>\n\n"
                f"<b>{home}  {score_str}  {away}</b>\n\n"
                f"{result}{suffix}"
                f"{breakdown}\n\n"
                f"{_context_line()}\n\n"
                f"<i>FIFA World Cup 2026 · GSA Gateway</i>"
            )
    else:
        return

    await tg.broadcast(text, parse_mode="HTML")


# ── Cog ───────────────────────────────────────────────────────────────────────

class SchedulerCog(commands.Cog, name="Scheduler"):
    """Background task runner for reminders and daily digest."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.mathcafe = getattr(bot, "mathcafe", None)
        self._kb_reminders_sent: set[tuple[str, str]] = set()
        self.football_client = None
        self.worldcup_tracker = None

        if config.football_enabled and config.football_api_key:
            from bot.services.football_client import FootballClient
            from bot.services.worldcup_tracker import WorldCupTracker
            self.football_client = FootballClient(config.football_api_key)
            self.worldcup_tracker = WorldCupTracker(self.football_client)
            # Also expose on the bot so the /worldcup command can reach them
            bot.football_client = self.football_client
            bot.worldcup_tracker = self.worldcup_tracker
            logger.info("World Cup tracker initialized")
            self.check_worldcup.start()
            self.worldcup_daily_schedule.start()
        else:
            logger.info("World Cup disabled — set FOOTBALL_ENABLED=true to enable")

        self.check_upcoming_reminders.start()
        self.daily_digest.start()
        if config.mathcafe_enabled:
            self.post_mathcafe_daily.start()
        else:
            logger.info("MathCafe daily post disabled — set MATHCAFE_ENABLED=true to enable")

    def cog_unload(self) -> None:
        if self.football_client:
            self.check_worldcup.cancel()
            self.worldcup_daily_schedule.cancel()
        self.check_upcoming_reminders.cancel()
        self.daily_digest.cancel()
        if self.post_mathcafe_daily.is_running():
            self.post_mathcafe_daily.cancel()

    def _get_guild(self) -> Optional[discord.Guild]:
        if config.discord_guild_id:
            return self.bot.get_guild(config.discord_guild_id)
        guilds = self.bot.guilds
        return guilds[0] if guilds else None

    # ── Reminder task ──────────────────────────────────────────────────────────

    def _kb_events_not_in_db(self, db_names: set[str]) -> list[dict]:
        """Return KB (YAML) events that aren't already tracked in the DB."""
        kb = getattr(self.bot, "kb", None)
        if kb is None:
            return []
        today = datetime.date.today().isoformat()
        result = []
        for ev in kb.events:
            if ev.date >= today and ev.name not in db_names:
                result.append({
                    "name": ev.name,
                    "date": ev.date,
                    "time": ev.time,
                    "location": ev.location,
                    "description": ev.description,
                    "organizer": ev.organizer,
                    "rsvp_link": ev.rsvp_link,
                    "category": ev.category,
                })
        return result

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
            db_events = db.get_events_for_reminders()
        except Exception as exc:
            logger.error("Scheduler: DB error fetching events: %s", exc)
            db_events = []

        for event in db_events:
            try:
                await self._process_reminders(guild, event, now, today)
            except Exception as exc:
                logger.error(
                    "Scheduler: error processing reminder for '%s': %s",
                    event.get("name"), exc,
                )

        db_names = {e["name"] for e in db_events}
        for event in self._kb_events_not_in_db(db_names):
            try:
                await self._process_reminders(guild, event, now, today, is_kb=True)
            except Exception as exc:
                logger.error(
                    "Scheduler: error processing KB reminder for '%s': %s",
                    event.get("name"), exc,
                )

    async def _process_reminders(
        self,
        guild: discord.Guild,
        event: dict,
        now: datetime.datetime,
        today: datetime.date,
        *,
        is_kb: bool = False,
    ) -> None:
        db = self.bot.db  # type: ignore[attr-defined]
        try:
            event_date = datetime.date.fromisoformat(event["date"])
        except (ValueError, KeyError):
            return

        delta = (event_date - today).days
        name  = event.get("name", "")

        def _already_sent(key: str) -> bool:
            if is_kb:
                return (name, key) in self._kb_reminders_sent
            return bool(event.get(f"reminder_sent_{key}"))

        def _mark_sent(key: str) -> None:
            if is_kb:
                self._kb_reminders_sent.add((name, key))
            else:
                db.mark_reminder_sent(event["id"], key)

        if delta == 7 and not _already_sent("7d"):
            await self._send_reminder(guild, event, "reminder_7d", db)
            _mark_sent("7d")

        elif delta == 1 and not _already_sent("1d"):
            await self._send_reminder(guild, event, "reminder_1d", db)
            _mark_sent("1d")

        elif delta == 0 and not _already_sent("1h"):
            event_dt = _parse_start_time(event["date"], event.get("time", "TBD"))
            if event_dt and 0 <= (event_dt - now).total_seconds() <= 3600:
                await self._send_reminder(guild, event, "reminder_1h", db)
                _mark_sent("1h")

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

        # ── Telegram mirror ────────────────────────────────────────────────────
        tg = getattr(self.bot, "telegram_connector", None)
        if tg:
            name     = event.get("name", "Event")
            date_val = event.get("date", "")
            time_val = event.get("time", "TBD") or "TBD"
            location = event.get("location", "TBD") or "TBD"
            rsvp     = event.get("rsvp_link", "")

            if reminder_type == "reminder_7d":
                header = f"📅 <b>Coming next week: {name}</b>"
            elif reminder_type == "reminder_1d":
                header = f"⏰ <b>Tomorrow: {name}</b>"
            else:
                header = f"🔔 <b>Starting in 1 hour: {name}</b>"

            tg_text = f"{header}\n\n📅 {date_val} · {time_val}\n📍 {location}"
            if rsvp:
                tg_text += f"\n\n<a href=\"{rsvp}\">Register / RSVP</a>"
            tg_text += "\n\n<i>NJIT Graduate Student Association</i>"

            try:
                await tg.broadcast(tg_text, parse_mode="HTML")
            except Exception as exc:
                logger.warning("Telegram reminder broadcast failed: %s", exc)

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

    # ── World Cup live polling task ────────────────────────────────────────────

    @tasks.loop(seconds=60)
    async def check_worldcup(self) -> None:
        """Poll football API and post live match events to the World Cup channel."""
        if not config.football_enabled or not self.worldcup_tracker:
            return
        try:
            events = await self.worldcup_tracker.check_matches()
            if not events:
                return

            guild = self._get_guild()
            if guild is None:
                return

            channel = discord.utils.get(
                guild.text_channels, name=config.football_channel
            )
            if channel is None:
                logger.warning(
                    "World Cup channel '%s' not found in Discord server",
                    config.football_channel,
                )
                return

            from bot.services.worldcup_embeds import (
                build_kickoff_embed,
                build_goal_embed,
                build_halftime_embed,
                build_second_half_embed,
                build_fulltime_embed,
            )

            for event in events:
                etype = event["type"]
                if etype == "kickoff":
                    embed = build_kickoff_embed(event["match"], self.worldcup_tracker)
                elif etype == "goal":
                    embed = build_goal_embed(event, self.worldcup_tracker)
                elif etype == "halftime":
                    embed = build_halftime_embed(event["match"], self.worldcup_tracker)
                elif etype == "second_half":
                    embed = build_second_half_embed(event["match"], self.worldcup_tracker)
                elif etype == "fulltime":
                    embed = build_fulltime_embed(event, self.worldcup_tracker)
                else:
                    continue
                await channel.send(embed=embed)
                await _broadcast_wc_event(self.bot, event, self.worldcup_tracker)
                await asyncio.sleep(1)

        except Exception as exc:
            logger.error("World Cup check failed: %s", exc, exc_info=True)

    @check_worldcup.before_loop
    async def _before_worldcup(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(time=_WC_SCHEDULE_TIME)
    async def worldcup_daily_schedule(self) -> None:
        """Post today's World Cup match schedule at 8 AM ET."""
        if not config.football_enabled or not self.football_client:
            return
        try:
            matches = await self.football_client.get_todays_matches()
            if not matches:
                return

            guild = self._get_guild()
            if guild is None:
                return

            channel = discord.utils.get(
                guild.text_channels, name=config.football_channel
            )
            if channel is None:
                return

            from bot.services.worldcup_embeds import build_daily_schedule_embed, kickoff_to_et, format_stage, format_group
            embed = build_daily_schedule_embed(matches, self.worldcup_tracker)
            await channel.send(embed=embed)
            logger.info("World Cup daily schedule posted: %d matches", len(matches))

            tg = getattr(self.bot, "telegram_connector", None)
            if tg and matches:
                lines = ["⚽ <b>Today's World Cup Matches</b>\n"]
                for m in matches:
                    h = self.worldcup_tracker.format_team_name(m["homeTeam"])
                    a = self.worldcup_tracker.format_team_name(m["awayTeam"])
                    t = kickoff_to_et(m["utcDate"])
                    lines.append(f"<b>{h} vs {a}</b>")
                    lines.append(f"⏰ {t}\n")
                lines.append("<i>All times Eastern · GSA Gateway</i>")
                await tg.broadcast("\n".join(lines), parse_mode="HTML")

        except Exception as exc:
            logger.error("World Cup daily schedule failed: %s", exc, exc_info=True)

    @worldcup_daily_schedule.before_loop
    async def _before_wc_schedule(self) -> None:
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
            tg = getattr(self.bot, "telegram_connector", None)
            fact = getattr(mathcafe, "last_posted_fact", None)
            if tg and fact:
                title = fact.get("title", "")
                body = fact.get("body", "")
                footer = fact.get("footer", "GSA MathCafe")
                text = f"☕ <b>GSA MathCafe</b>\n\n<b>{title}</b>\n\n{body}"
                if fact.get("has_spoiler") and fact.get("spoiler_text"):
                    text += f"\n\n<tg-spoiler>{fact['spoiler_text']}</tg-spoiler>"
                text += f"\n\n<i>{footer}</i>"
                needs_image = fact.get("needs_image") and fact.get("image_filename")
                if needs_image:
                    image_path = f"bot/data/mathcafe/images/{fact['image_filename']}"
                    await tg.broadcast_photo(photo_path=image_path, caption=text, parse_mode="HTML")
                else:
                    await tg.broadcast(text, parse_mode="HTML")
                logger.info("MathCafe broadcast to Telegram")
        else:
            logger.warning("MathCafe daily post failed — no facts available")

    @post_mathcafe_daily.before_loop
    async def _before_mathcafe(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SchedulerCog(bot))
