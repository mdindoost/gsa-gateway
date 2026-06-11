"""DailyFixturesSource — World Cup schedule generator (a second WC content source).

Where ``worldcup_runner`` posts LIVE in-play events (kickoff/goal/half-time/full-
time), this generator posts a scheduled "what's on" digest: the day's fixtures
with kickoff times (US Eastern, for the NJIT audience), teams, and group/stage.

It's another worked example of the generator contract — pure data → ``PostDraft``
→ ``poll()`` — and produces NOTHING on days with no fixtures (poll returns []).
The validation, dedup, persistence and Discord+Telegram fan-out are all handled
by ``enqueue_post`` + the existing scheduler/connectors.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
from zoneinfo import ZoneInfo

import aiohttp

from v2.core.publishing.sources import PostDraft, PostSource
from v2.integration.worldcup_tracker import BASE_URL, team_label

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")  # NJIT / GSA audience is US Eastern

# Clean labels for the knockout stages (football-data.org stage codes).
STAGE_LABELS = {
    "GROUP_STAGE": "Group Stage", "LAST_16": "Round of 16", "ROUND_OF_16": "Round of 16",
    "QUARTER_FINALS": "Quarter-finals", "SEMI_FINALS": "Semi-finals",
    "THIRD_PLACE": "Third-place Play-off", "FINAL": "Final",
}


async def fetch_fixtures(api_key: str, day: datetime.date) -> list[dict]:
    """Fetch the World Cup matches scheduled on ``day``. Returns [] on any API
    error (so a bad fetch degrades to "no post", never a crash)."""
    iso = day.isoformat()
    url = f"{BASE_URL}/competitions/WC/matches?dateFrom={iso}&dateTo={iso}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"X-Auth-Token": api_key},
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning("WC fixtures API HTTP %d for %s", resp.status, iso)
                    return []
                data = await resp.json()
                return data.get("matches", [])
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning("WC fixtures API unreachable for %s: %s", iso, exc)
        return []


def _kickoff_et(utc_iso: str) -> str:
    """'2026-06-11T19:00:00Z' -> '3:00 PM ET' (US Eastern, DST-aware)."""
    try:
        dt = datetime.datetime.fromisoformat(utc_iso.replace("Z", "+00:00")).astimezone(ET)
    except (ValueError, AttributeError, TypeError):
        return "TBD"
    return dt.strftime("%I:%M %p").lstrip("0") + " ET"


def _context(match: dict) -> str:
    """Human label for the group or knockout stage."""
    grp = match.get("group")
    if grp:
        return grp.replace("GROUP_", "Group ")
    stage = match.get("stage") or ""
    return STAGE_LABELS.get(stage, stage.replace("_", " ").title())


def _team(team: dict | None) -> str:
    """Flagged team label, falling back to 'TBD' for unseeded knockout slots
    (the API sends name=None / '' before teams qualify)."""
    label = team_label(team or {}).strip()
    return label if label and label != "⚽" else "⚽ TBD"


def _fixture_line(match: dict) -> str:
    home = _team(match.get("homeTeam"))
    away = _team(match.get("awayTeam"))
    kickoff = _kickoff_et(match.get("utcDate", ""))
    ctx = _context(match)
    tail = f" · {ctx}" if ctx else ""
    return f"{home} vs {away} — {kickoff}{tail}"


def format_fixtures(day: datetime.date, matches: list[dict]) -> str:
    """Render the day's fixtures as one digest, ordered by kickoff time."""
    header = f"📅 **World Cup fixtures — {day.strftime('%A, %B')} {day.day}**"
    lines = [_fixture_line(m) for m in sorted(matches, key=lambda m: m.get("utcDate", ""))]
    return header + "\n\n" + "\n".join(lines)


def build_fixtures_draft(org_id: int, day: datetime.date, matches: list[dict],
                         channels: list[str] | None = None,
                         discord_channel: str | None = "world-cup-2026") -> PostDraft | None:
    """Build the schedule PostDraft for ``day``. Returns None when there are no
    fixtures that day (so the caller posts nothing)."""
    if not matches:
        return None
    return PostDraft(
        org_id=org_id,
        content=format_fixtures(day, matches),
        type="broadcast",
        channels=channels if channels is not None else ["discord", "telegram"],
        discord_channel=discord_channel,
        source_type="wc_fixtures",
        dedup_key=day.isoformat(),  # one schedule digest per day
        metadata={"date": day.isoformat(), "match_count": len(matches)},
    )


class DailyFixturesSource(PostSource):
    """Poll-style generator: each tick offers the target day's fixtures digest
    (deduped per day). ``day_offset`` selects today (0) or e.g. tomorrow (1).

    Recommended SourceRunner interval: HOURLY (3600s) or longer. The per-day
    dedup makes re-runs no-ops, so each tick also makes an API call — frequent
    polling only burns football-data.org's free-tier quota (~10 req/min) for no
    benefit. Once a day actually posts; an hourly check is plenty of slack."""

    name = "wc_fixtures"

    def __init__(self, api_key: str, org_id: int, channels: list[str] | None = None,
                 discord_channel: str | None = "world-cup-2026", day_offset: int = 0):
        self.api_key = api_key
        self.org_id = org_id
        self.channels = channels
        self.discord_channel = discord_channel
        self.day_offset = day_offset

    async def poll(self) -> list[PostDraft]:
        day = datetime.date.today() + datetime.timedelta(days=self.day_offset)
        matches = await fetch_fixtures(self.api_key, day)
        draft = build_fixtures_draft(self.org_id, day, matches,
                                     channels=self.channels,
                                     discord_channel=self.discord_channel)
        return [draft] if draft else []
