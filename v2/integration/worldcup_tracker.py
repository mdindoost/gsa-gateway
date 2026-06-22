"""v2 World Cup tracker — detection ported from v1, with the fetch bug fixed.

Polls football-data.org, diffs match state to detect new events (kickoff, goal,
halftime, second-half, full-time), and emits them as unified rich-text messages
for the connector registry (one message → Discord + Telegram).

The v1 client reused one long-lived aiohttp.ClientSession, whose pooled
connection went stale between polls (100% failures). Here every request uses a
fresh session with retries — the API is itself flaky (~1/6 disconnects), so
retries + the 60s poll cadence make coverage effectively complete.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://api.football-data.org/v4"
STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "worldcup_state.json"
# Optional raw-response debug log (set FOOTBALL_DEBUG_LOG=true): one line per poll
# showing what the API actually returned — status/score/minute/lastUpdated + which
# key — so the cache flapping (fresh vs stale responses) is visible.
DEBUG_FILE = Path(__file__).resolve().parents[2] / "logs" / "wc_api_debug.log"

MATCH_STATUS = {
    "SCHEDULED": "scheduled", "TIMED": "scheduled", "IN_PLAY": "live",
    "PAUSED": "halftime", "FINISHED": "finished",
    "POSTPONED": "postponed", "CANCELLED": "cancelled",
}

FLAG_MAP = {
    "Brazil": "🇧🇷", "Argentina": "🇦🇷", "France": "🇫🇷", "Germany": "🇩🇪",
    "Spain": "🇪🇸", "England": "🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",
    "Portugal": "🇵🇹", "Netherlands": "🇳🇱", "USA": "🇺🇸", "Mexico": "🇲🇽",
    "Japan": "🇯🇵", "South Korea": "🇰🇷", "Morocco": "🇲🇦", "Senegal": "🇸🇳",
    "Iran": "🇮🇷", "Saudi Arabia": "🇸🇦", "Australia": "🇦🇺", "Canada": "🇨🇦",
    "Croatia": "🇭🇷", "Serbia": "🇷🇸", "Switzerland": "🇨🇭", "Belgium": "🇧🇪",
    "Uruguay": "🇺🇾", "Colombia": "🇨🇴", "Ecuador": "🇪🇨", "Peru": "🇵🇪",
    "Chile": "🇨🇱", "Nigeria": "🇳🇬", "Ghana": "🇬🇭", "Cameroon": "🇨🇲",
    "Italy": "🇮🇹", "Poland": "🇵🇱", "Denmark": "🇩🇰", "Austria": "🇦🇹",
    "Turkey": "🇹🇷", "Ukraine": "🇺🇦", "Qatar": "🇶🇦", "Costa Rica": "🇨🇷",
    "Panama": "🇵🇦", "Honduras": "🇭🇳", "Jamaica": "🇯🇲", "Venezuela": "🇻🇪",
    "Bolivia": "🇧🇴", "Paraguay": "🇵🇾", "Algeria": "🇩🇿", "Tunisia": "🇹🇳",
    "Egypt": "🇪🇬", "Mali": "🇲🇱", "Ivory Coast": "🇨🇮", "South Africa": "🇿🇦",
    "Indonesia": "🇮🇩", "Thailand": "🇹🇭", "Vietnam": "🇻🇳", "Iraq": "🇮🇶",
    "United Arab Emirates": "🇦🇪", "New Zealand": "🇳🇿", "El Salvador": "🇸🇻",
    "Cuba": "🇨🇺", "Trinidad and Tobago": "🇹🇹", "Bahrain": "🇧🇭",
    "Jordan": "🇯🇴", "Palestine": "🇵🇸", "Uzbekistan": "🇺🇿",
    "New Caledonia": "🇳🇨", "Czechia": "🇨🇿", "Bosnia-Herzegovina": "🇧🇦",
    "Slovakia": "🇸🇰", "Slovenia": "🇸🇮", "Albania": "🇦🇱", "Georgia": "🇬🇪",
    "Scotland": "🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f",
    "Wales": "🏴\U000e0067\U000e0062\U000e0077\U000e006c\U000e0073\U000e007f",
    "Romania": "🇷🇴", "Hungary": "🇭🇺", "Czech Republic": "🇨🇿",
    "North Macedonia": "🇲🇰", "Iceland": "🇮🇸", "Finland": "🇫🇮",
    "Norway": "🇳🇴", "Sweden": "🇸🇪", "Greece": "🇬🇷", "Cape Verde": "🇨🇻",
    "Angola": "🇦🇴", "Tanzania": "🇹🇿", "Zambia": "🇿🇲", "Guinea": "🇬🇳",
    "Mozambique": "🇲🇿",
    # football-data.org API spellings that differ from the common names above
    "United States": "🇺🇸", "Cape Verde Islands": "🇨🇻", "Congo DR": "🇨🇩",
    "Curaçao": "🇨🇼", "Haiti": "🇭🇹",
}


@dataclass
class MatchState:
    match_id: int
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    status: str
    minute: int
    goals_announced: list
    kickoff_announced: bool
    halftime_announced: bool
    second_half_announced: bool
    fulltime_announced: bool
    stage: str
    group: str
    utc_date: str
    preview_announced: bool = False


def flag(name: str) -> str:
    return FLAG_MAP.get(name, "⚽")


def team_label(team: dict) -> str:
    name = team.get("name", "") or team.get("shortName", "")
    return f"{flag(name)} {name}"


class WorldCupTracker:
    """Fetches match data and diffs it into events. No platform knowledge."""

    def __init__(self, api_key: str):
        # One or more comma-separated keys. Requests round-robin across them, so
        # the effective budget is N × the per-key 10/min limit — and a 429 on one
        # key just retries on the next. Add a key, poll faster.
        self.keys = [k.strip() for k in (api_key or "").split(",") if k.strip()] or [api_key]
        self._key_idx = 0
        self._last_key = ""
        self.debug_log = os.getenv("FOOTBALL_DEBUG_LOG", "false").lower() == "true"
        self.states: dict[int, MatchState] = {}
        # The per-goal scorer/minute feed (X-Unfold-Goals) is a PAID feature; the
        # free tier returns no goals array, so calling it on every score change
        # just burns the rate-limit budget. Off by default — flip on if upgraded.
        self.unfold_goals = os.getenv("FOOTBALL_UNFOLD_GOALS", "false").lower() == "true"
        # Squad/coach roster (/competitions/WC/teams) — memoized for the process
        # lifetime, but ONLY a successful, non-empty result is cached (a flaky {}
        # must not permanently zero out previews). name -> team entry.
        self._teams_cache: dict[str, dict] = {}
        self.load_state()

    def _next_headers(self, extra: dict | None = None) -> dict:
        """Round-robin the next API key into the request headers."""
        key = self.keys[self._key_idx % len(self.keys)]
        self._key_idx += 1
        self._last_key = key
        return {"X-Auth-Token": key, **(extra or {})}

    # ── HTTP (fresh session per call + retries — the fix) ─────────────────────
    async def _get(self, endpoint: str, extra_headers: dict | None = None, retries: int = 3) -> dict:
        url = BASE_URL + endpoint
        last = None
        for attempt in range(retries):
            headers = self._next_headers(extra_headers)  # round-robin key per attempt
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers,
                                           timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 429:
                            # this key is throttled — the next attempt uses the next
                            # key; only hard-back-off when there's a single key.
                            logger.warning("WC API rate-limited on a key; rotating to next")
                            if len(self.keys) == 1:
                                await asyncio.sleep(5)
                            continue
                        if resp.status != 200:
                            logger.warning("WC API HTTP %d for %s", resp.status, endpoint)
                            return {}
                        return await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last = exc
                await asyncio.sleep(0.5 * (attempt + 1))
        logger.warning("WC API unreachable after %d tries (%s): %s", retries, endpoint, last)
        return {}

    async def get_todays_matches(self) -> list:
        data = await self._get("/competitions/WC/matches")
        today = datetime.date.today().isoformat()
        todays = [m for m in data.get("matches", []) if m.get("utcDate", "")[:10] == today]
        self._debug(len(data.get("matches", [])), todays)
        return todays

    async def fetch_standings(self) -> dict[str, list[dict]]:
        """Return {group_token: [table_rows]} for the WC group stage. {} on failure.

        Reuses ``_get`` (round-robin keys, returns {} on any HTTP/network error,
        never raises). Keeps only blocks that carry a ``group`` — knockout blocks
        have ``group=None`` and are dropped. Same payload the dashboard embed at
        ``bot/services/worldcup_embeds.py`` consumes."""
        data = await self._get("/competitions/WC/standings")
        out: dict[str, list[dict]] = {}
        for block in data.get("standings", []):
            g = block.get("group")
            if g:
                # The standings endpoint labels groups "Group H" but the matches
                # endpoint (and match['group']) uses "GROUP_H". Normalize to the
                # matches format so kickoff/preview lookups by match['group'] resolve.
                out[g.upper().replace(" ", "_")] = block.get("table", [])
        return out

    def _debug(self, total: int, matches: list) -> None:
        """Append one line per poll showing exactly what the API returned (fresh
        vs stale), keyed by which API key served it. Never raises."""
        if not self.debug_log:
            return
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        key = self._last_key[-4:] if self._last_key else "----"
        try:
            DEBUG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(DEBUG_FILE, "a", encoding="utf-8") as f:
                if not matches:
                    f.write(f"{ts} key=…{key} total={total} today=0\n")
                for m in matches:
                    ft = (m.get("score") or {}).get("fullTime") or {}
                    f.write(f"{ts} key=…{key} {(m.get('homeTeam') or {}).get('name')} v "
                            f"{(m.get('awayTeam') or {}).get('name')} status={m.get('status')} "
                            f"score={ft.get('home')}-{ft.get('away')} min={m.get('minute')} "
                            f"lastUpdated={m.get('lastUpdated')}\n")
        except Exception:
            pass  # debug logging must never break the tracker

    async def get_match(self, match_id: int) -> dict:
        return await self._get(f"/matches/{match_id}", {"X-Unfold-Goals": "true"})

    async def health_check(self) -> bool:
        data = await self._get("/competitions/WC/matches")
        return bool(data.get("matches") is not None)

    # ── state persistence ─────────────────────────────────────────────────────
    def load_state(self) -> None:
        if not STATE_FILE.exists():
            return
        try:
            raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            for mid_str, f in raw.items():
                mid = int(mid_str)
                self.states[mid] = MatchState(
                    match_id=f.get("match_id", mid), home_team=f.get("home_team", ""),
                    away_team=f.get("away_team", ""), home_score=f.get("home_score", 0),
                    away_score=f.get("away_score", 0), status=f.get("status", "scheduled"),
                    minute=f.get("minute", 0), goals_announced=f.get("goals_announced", []),
                    kickoff_announced=f.get("kickoff_announced", False),
                    halftime_announced=f.get("halftime_announced", False),
                    second_half_announced=f.get("second_half_announced", False),
                    fulltime_announced=f.get("fulltime_announced", False),
                    stage=f.get("stage", ""), group=f.get("group", ""), utc_date=f.get("utc_date", ""),
                    preview_announced=f.get("preview_announced", False))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Could not load worldcup state: %s", exc)
            self.states = {}

    def save_state(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {str(mid): dataclasses.asdict(s) for mid, s in self.states.items()}
        STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ── detection (ported from v1 verbatim) ───────────────────────────────────
    async def check_matches(self) -> list[dict]:
        matches = await self.get_todays_matches()
        events: list[dict] = []
        for match in matches:
            match_id = match["id"]
            raw_status = match.get("status", "")
            status = MATCH_STATUS.get(raw_status, raw_status.lower())
            home_score = match["score"]["fullTime"]["home"] or 0
            away_score = match["score"]["fullTime"]["away"] or 0
            minute = match.get("minute") or 0
            prev = self.states.get(match_id)

            # KICKOFF
            if raw_status == "IN_PLAY" and (prev is None or prev.status == "scheduled") \
                    and (prev is None or not prev.kickoff_announced):
                events.append({"type": "kickoff", "match": match})
                kickoff_announced = True
            else:
                kickoff_announced = prev.kickoff_announced if prev else False

            # GOAL via score diff
            prev_home = prev.home_score if prev else 0
            prev_away = prev.away_score if prev else 0
            if home_score > prev_home or away_score > prev_away:
                all_goals = []
                if self.unfold_goals:  # paid tier only; skip the wasted call on free tier
                    full_match = await self.get_match(match_id)
                    all_goals = (full_match or {}).get("goals", [])
                announced_goals = list(prev.goals_announced) if prev else []
                if all_goals:
                    for goal in all_goals:
                        goal_key = {"minute": goal.get("minute"),
                                    "scorer": goal.get("scorer", {}).get("name", ""),
                                    "team": goal.get("team", {}).get("name", "")}
                        if goal_key not in announced_goals:
                            events.append({"type": "goal", "match": match,
                                           "scorer": goal.get("scorer", {}).get("name", ""),
                                           "team": goal.get("team", {}).get("name", ""),
                                           "minute": goal.get("minute")})
                            announced_goals.append(goal_key)
                else:
                    home_delta = home_score - prev_home
                    away_delta = away_score - prev_away
                    new_score_key = {"score": f"{home_score}-{away_score}"}
                    if new_score_key not in announced_goals:
                        for _ in range(home_delta):
                            events.append({"type": "goal", "match": match, "scoring_team": match["homeTeam"]})
                        for _ in range(away_delta):
                            events.append({"type": "goal", "match": match, "scoring_team": match["awayTeam"]})
                        announced_goals.append(new_score_key)
            else:
                announced_goals = list(prev.goals_announced) if prev else []

            # HALFTIME
            if raw_status == "PAUSED" and prev and prev.status == "live" and not prev.halftime_announced:
                events.append({"type": "halftime", "match": match})
                halftime_announced = True
            else:
                halftime_announced = prev.halftime_announced if prev else False

            # SECOND HALF
            if raw_status == "IN_PLAY" and prev and prev.status == "halftime" \
                    and not prev.second_half_announced:
                events.append({"type": "second_half", "match": match})
                second_half_announced = True
            else:
                second_half_announced = prev.second_half_announced if prev else False

            # FULLTIME
            if raw_status == "FINISHED" and prev and prev.status in ("live", "halftime") \
                    and not prev.fulltime_announced:
                events.append({"type": "fulltime", "match": match})
                fulltime_announced = True
            else:
                fulltime_announced = prev.fulltime_announced if prev else False

            self.states[match_id] = MatchState(
                match_id=match_id, home_team=match["homeTeam"]["name"],
                away_team=match["awayTeam"]["name"], home_score=home_score, away_score=away_score,
                status=status, minute=minute, goals_announced=announced_goals,
                kickoff_announced=kickoff_announced, halftime_announced=halftime_announced,
                second_half_announced=second_half_announced, fulltime_announced=fulltime_announced,
                stage=match.get("stage", ""), group=match.get("group", "") or "",
                utc_date=match.get("utcDate", ""),
                preview_announced=prev.preview_announced if prev else False)

        self.save_state()
        return events

    # ── pre-match preview (separate window + trigger) ──────────────────────────
    async def upcoming_for_preview(self) -> list[dict]:
        """TIMED/SCHEDULED matches over a 2-day UTC window [today, today+1].

        The preview fires ~90 min BEFORE kickoff, and a US-evening kickoff has its
        ``utcDate`` on the NEXT UTC day — so this canNOT ride on ``get_todays_matches``
        (today-only). Same dateFrom/dateTo window ``daily_fixtures.fetch_fixtures`` uses."""
        today = datetime.date.today()
        nxt = today + datetime.timedelta(days=1)
        data = await self._get(
            f"/competitions/WC/matches?dateFrom={today.isoformat()}&dateTo={nxt.isoformat()}")
        return [m for m in data.get("matches", []) if m.get("status") in ("TIMED", "SCHEDULED")]

    async def check_previews(self, now: datetime.datetime | None = None) -> list[dict]:
        """Emit a one-time ``preview`` event per match in the [kickoff-LEAD, kickoff)
        window. Gated by ``FOOTBALL_PREVIEW_ENABLED`` (default on); lead minutes from
        ``FOOTBALL_PREVIEW_LEAD_MIN`` (default 5 — fires just as people sit down to
        watch). Fires once via ``preview_announced``; the durable once-guard is the
        runner's persisted ``posts`` dedup row."""
        if os.getenv("FOOTBALL_PREVIEW_ENABLED", "true").lower() == "false":
            return []
        lead = int(os.getenv("FOOTBALL_PREVIEW_LEAD_MIN", "5"))
        now = now or datetime.datetime.now(datetime.timezone.utc)
        events: list[dict] = []
        for m in await self.upcoming_for_preview():
            mid = m.get("id")
            prev = self.states.get(mid)
            if prev and prev.preview_announced:
                continue
            try:
                kickoff = datetime.datetime.fromisoformat(
                    (m.get("utcDate") or "").replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            if kickoff - datetime.timedelta(minutes=lead) <= now < kickoff:
                events.append({"type": "preview", "match": m})
                self._mark_previewed(m)
        if events:
            self.save_state()
        return events

    def _mark_previewed(self, m: dict) -> None:
        mid = m["id"]
        prev = self.states.get(mid)
        if prev:
            prev.preview_announced = True
        else:
            self.states[mid] = MatchState(
                match_id=mid, home_team=m["homeTeam"]["name"], away_team=m["awayTeam"]["name"],
                home_score=0, away_score=0, status="scheduled", minute=0, goals_announced=[],
                kickoff_announced=False, halftime_announced=False, second_half_announced=False,
                fulltime_announced=False, stage=m.get("stage", ""), group=m.get("group", "") or "",
                utc_date=m.get("utcDate", ""), preview_announced=True)

    async def fetch_teams(self) -> dict[str, dict]:
        """name -> /competitions/WC/teams entry (squad + coach). Memoized on first
        SUCCESS only — a flaky {} is returned but NOT cached, so the next call retries."""
        if self._teams_cache:
            return self._teams_cache
        data = await self._get("/competitions/WC/teams")
        teams = {t["name"]: t for t in data.get("teams", []) if t.get("name")}
        if teams:
            self._teams_cache = teams
        return teams

    async def fetch_h2h(self, match_id: int) -> dict:
        """Raw /matches/{id}/head2head payload ({} on any error — never raises)."""
        return await self._get(f"/matches/{match_id}/head2head")


# ── unified rich-text formatting (one message, both platforms) ───────────────
def _score_line(match: dict) -> str:
    ft = match.get("score", {}).get("fullTime", {})
    hs, as_ = ft.get("home") or 0, ft.get("away") or 0
    return f"{team_label(match['homeTeam'])}  {hs}–{as_}  {team_label(match['awayTeam'])}"


def _context(match: dict) -> str:
    bits = []
    if match.get("stage"):
        bits.append(match["stage"].replace("_", " ").title())
    if match.get("group"):
        bits.append(match["group"].replace("_", " ").title())
    return " · ".join(bits)


def format_standings(group: str, rows: list[dict]) -> str:
    """One line per team — monospace-free so it renders on every channel.

    Discord/Telegram show the bold header; GroupMe (plain text) strips it. NO code
    fences: Telegram HTML-escapes them and GroupMe shows them literally, and neither
    keeps monospace column alignment. So each team is a self-contained line instead
    of a grid. Returns "" for an empty table (caller then appends nothing)."""
    if not rows:
        return ""
    label = group.replace("_", " ").title()
    lines = [f"📊 **{label}**"]
    for r in rows:
        name = (r.get("team") or {}).get("name") or "?"
        if len(name) > 28:
            name = name[:27] + "…"
        pts = r.get("points") or 0
        gd = r.get("goalDifference") or 0
        gd_str = f"+{gd}" if gd > 0 else str(gd)
        lines.append(
            f"{r.get('position') or 0}. {name} — {pts} pt{'' if pts == 1 else 's'} · GD {gd_str}"
        )
    return "\n".join(lines)


def format_event(ev: dict) -> str:
    match = ev["match"]
    etype = ev["type"]
    if etype == "kickoff":
        ctx = _context(match)
        head = f"⚽ **KICK-OFF!**\n{team_label(match['homeTeam'])} vs {team_label(match['awayTeam'])}"
        return f"{head}\nThe match is underway! 🌍" + (f"\n_{ctx}_" if ctx else "")
    if etype == "goal":
        tail = f"\n_{ev['half_label']}_" if ev.get("half_label") else ""
        if ev.get("scorer"):
            minute = f" {ev['minute']}'" if ev.get("minute") else ""
            return f"🥅 **GOAL!** {flag(ev.get('team',''))} {ev['scorer']}{minute}\n{_score_line(match)}{tail}"
        scorer_team = ev.get("scoring_team", {})
        return f"🥅 **GOAL!** {team_label(scorer_team)}\n{_score_line(match)}{tail}"
    if etype == "correction":
        return f"⚠️ **Score correction**\n{_score_line(match)}"
    if etype == "halftime":
        return f"⏸️ **HALF-TIME**\n{_score_line(match)}"
    if etype == "second_half":
        return f"▶️ **Second half underway**\n{_score_line(match)}"
    if etype == "fulltime":
        return f"🏁 **FULL-TIME**\n{_score_line(match)}"
    return f"{team_label(match['homeTeam'])} vs {team_label(match['awayTeam'])}"
