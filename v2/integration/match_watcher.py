"""MatchWatcher — schedule-driven, burst-and-rest World Cup live poller.

A lighter strategy than the constant-polling WorldCupRunner, tuned to the free
tier's intermittent freshness (most reads are stale; only ~1 in 5 carries the
live state):

  * Idle until ~5 min before a game — no API calls between games.
  * Catch phase: poll the PRIMARY key every 10s to grab one live read. If a full
    minute yields none, BURST across all keys (primary first; the backup key is
    used only here) until one live read is caught.
  * First live read  -> "kick-off" (start) post.
    Higher score      -> the new scoreline (goal) post.
    FINISHED          -> full-time post using the STORED score (the FINISHED read
                         carries no score!), then stop watching this match.
  * Score is stored MONOTONICALLY, so a stale/empty read can never erase it.

Kickoff times come from the API (``utcDate`` is static schedule data and reliable,
unlike the live status/score).
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import os

import aiohttp

from v2.core.database.schema import get_connection
from v2.core.publishing.sources import EnqueueError, PostDraft, enqueue_post, platform_channels
from v2.integration.worldcup_tracker import BASE_URL, DEBUG_FILE, format_event

logger = logging.getLogger(__name__)

LIVE = {"IN_PLAY", "PAUSED"}      # carries the live score
DONE = {"FINISHED"}               # end-of-game signal (no score!)
CATCHABLE = LIVE | DONE

PRE_KICKOFF_LEAD = datetime.timedelta(minutes=5)
REST_SECONDS = 1 * 60             # rest after a successful catch — matches the API's ~1-min
                                  # score-refresh cadence; ≤1 min lag, ~1 read/min (10% of cap)
PRIMARY_TRIES = 6                 # primary-key reads (~1 min at 10s) before bursting
PRIMARY_INTERVAL = 10
BURST_TRIES = 12                  # rapid reads across all keys
BURST_INTERVAL = 2
MATCH_MAX = datetime.timedelta(hours=2, minutes=30)   # safety stop after kickoff
KICKOFF_GRACE = datetime.timedelta(minutes=30)        # if the first live read is caught this
                                                      # soon after the scheduled kickoff it's
                                                      # still "the start" (the free API can
                                                      # report the live state late); catching
                                                      # it later means a mid-match restart


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class MatchWatcher:
    def __init__(self, keys, db_path: str, org_slug: str = "gsa",
                 channel: str = "world-cup-2026"):
        self.keys = keys if isinstance(keys, list) else \
            [k.strip() for k in (keys or "").split(",") if k.strip()]
        self.db_path = db_path
        self.org_slug = org_slug
        self.channel = channel
        self._conn = None
        self.org_id = None
        self._task = None
        self._running = False
        self.debug_log = os.getenv("FOOTBALL_DEBUG_LOG", "false").lower() == "true"

    # ── HTTP ──────────────────────────────────────────────────────────────────
    async def _get(self, key: str, url: str) -> dict | None:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, headers={"X-Auth-Token": key},
                                 timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        return await r.json()
                    logger.warning("MatchWatcher API HTTP %d", r.status)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("MatchWatcher API error: %s", exc)
        return None

    async def _fetch_match(self, key: str, match_id: int, et_day: str) -> dict | None:
        nxt = (datetime.date.fromisoformat(et_day) + datetime.timedelta(days=1)).isoformat()
        url = f"{BASE_URL}/competitions/WC/matches?dateFrom={et_day}&dateTo={nxt}"
        data = await self._get(key, url)
        m = next((x for x in data.get("matches", []) if x.get("id") == match_id), None) if data else None
        self._debug(key, m)
        return m

    def _debug(self, key: str, match: dict | None) -> None:
        """Append the raw read to logs/wc_api_debug.log when FOOTBALL_DEBUG_LOG=true."""
        if not self.debug_log:
            return
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        tag = f"…{key[-4:]}" if key else "----"
        try:
            DEBUG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(DEBUG_FILE, "a", encoding="utf-8") as f:
                if not match:
                    f.write(f"{ts} key={tag} (match not in response)\n")
                else:
                    ft = (match.get("score") or {}).get("fullTime") or {}
                    f.write(f"{ts} key={tag} {(match.get('homeTeam') or {}).get('name')} v "
                            f"{(match.get('awayTeam') or {}).get('name')} status={match.get('status')} "
                            f"score={ft.get('home')}-{ft.get('away')} "
                            f"lastUpdated={match.get('lastUpdated')}\n")
        except Exception:
            pass

    # ── pure state machine (testable; no I/O) ──────────────────────────────────
    @staticmethod
    def _parse(match: dict):
        ft = (match.get("score") or {}).get("fullTime") or {}
        return match.get("status"), (ft.get("home") or 0, ft.get("away") or 0)

    @staticmethod
    def _with_score(match: dict, score) -> dict:
        m = dict(match)
        m["score"] = {"fullTime": {"home": score[0], "away": score[1]}}
        return m

    def _process(self, match: dict, state: dict, near_kickoff: bool = False) -> list[dict]:
        """Given a catchable read + running state, return event dicts to post and
        mutate ``state`` (keys: started, score, finished).

        ``near_kickoff`` (True when we're still within KICKOFF_GRACE of the scheduled
        kickoff) decides what a non-0-0 FIRST live read means: a genuine start the API
        reported late (announce kickoff) vs. a mid-match restart (stay silent)."""
        status, (h, a) = self._parse(match)
        events: list[dict] = []
        if status in DONE:
            if not state["finished"]:
                state["finished"] = True
                # FINISHED carries no score — use what we stored during the match
                events.append({"type": "fulltime",
                               "match": self._with_score(match, state["score"])})
            return events
        if status in LIVE:
            if not state["started"]:
                state["started"] = True
                if (h, a) != (0, 0):
                    # Caught after the opening whistle. Adopt the current score as a SILENT
                    # baseline either way — never back-announce / mis-order goals already
                    # scored (a phantom 1-0 for a 0-1,1-1 game). But if we're still near the
                    # scheduled kickoff, the match genuinely just started and the API merely
                    # reported the live state late, so we STILL announce kickoff; far past
                    # kickoff it's a restart and we stay silent.
                    state["score"] = (h, a)
                    if near_kickoff:
                        events.append({"type": "kickoff", "match": match})
                    return events
                events.append({"type": "kickoff", "match": match})
            ph, pa = state["score"]
            nh, na = max(h, ph), max(a, pa)          # monotonic — never go down
            if (nh, na) != (ph, pa):
                # Walk the score up one goal at a time so each goal post shows its
                # own running scoreline AND gets a distinct dedup key (a single
                # read jumping 0-0→2-0 must produce "1-0" then "2-0", not two "2-0"
                # that would collide and drop the 2nd goal).
                cur_h, cur_a = ph, pa
                for _ in range(nh - ph):
                    cur_h += 1
                    events.append({"type": "goal", "scoring_team": match["homeTeam"],
                                   "match": self._with_score(match, (cur_h, cur_a))})
                for _ in range(na - pa):
                    cur_a += 1
                    events.append({"type": "goal", "scoring_team": match["awayTeam"],
                                   "match": self._with_score(match, (cur_h, cur_a))})
                state["score"] = (nh, na)
        return events

    @staticmethod
    def _dedup_key(match_id: int, ev: dict) -> str:
        if ev["type"] == "goal":
            s = ev["match"]["score"]["fullTime"]
            return f"{match_id}:goal:{s['home']}-{s['away']}"
        return f"{match_id}:{ev['type']}:"

    def _post(self, match_id: int, ev: dict) -> None:
        try:
            enqueue_post(self._conn, PostDraft(
                org_id=self.org_id, content=format_event(ev), type="worldcup",
                channels=platform_channels(), discord_channel=self.channel,
                source_type="worldcup", dedup_key=self._dedup_key(match_id, ev),
                metadata={"event_type": ev["type"]}))
            logger.info("MatchWatcher: posted %s for match %s", ev["type"], match_id)
        except EnqueueError as exc:
            logger.warning("MatchWatcher: dropped %s: %s", ev.get("type"), exc)

    # ── catch one live/finished read ──────────────────────────────────────────
    async def _catch(self, match_id: int, et_day: str) -> dict | None:
        """Primary key every 10s (~1 min); if none, burst across all keys.

        Budget: a full catch+burst spreads ~12 reads on the primary key over ~84s
        (~8.6/min) — under the 10/min/key cap. A stray 429 just yields a stale read
        and the loop continues, so brief overage is harmless."""
        for _ in range(PRIMARY_TRIES):
            if not self._running:
                return None
            m = await self._fetch_match(self.keys[0], match_id, et_day)
            if m and m.get("status") in CATCHABLE:
                return m
            await asyncio.sleep(PRIMARY_INTERVAL)
        for i in range(BURST_TRIES):                  # backup key used only here
            if not self._running:
                return None
            m = await self._fetch_match(self.keys[i % len(self.keys)], match_id, et_day)
            if m and m.get("status") in CATCHABLE:
                return m
            await asyncio.sleep(BURST_INTERVAL)
        return None

    # ── per-match loop ─────────────────────────────────────────────────────────
    async def _watch(self, match_id: int, et_day: str, kickoff_utc: datetime.datetime) -> None:
        wait = (kickoff_utc - PRE_KICKOFF_LEAD - _utcnow()).total_seconds()
        if wait > 0:
            logger.info("MatchWatcher: sleeping %.0fs until match %s window", wait, match_id)
            await asyncio.sleep(wait)
        state = {"started": False, "score": (0, 0), "finished": False}
        deadline = kickoff_utc + MATCH_MAX
        logger.info("MatchWatcher: watching match %s", match_id)
        while self._running and not state["finished"] and _utcnow() < deadline:
            m = await self._catch(match_id, et_day)
            if m:
                near = _utcnow() < kickoff_utc + KICKOFF_GRACE
                for ev in self._process(m, state, near):
                    self._post(match_id, ev)
                if state["finished"]:
                    break
                await asyncio.sleep(REST_SECONDS)      # caught one → rest, then re-read
        logger.info("MatchWatcher: match %s done (finished=%s score=%s)",
                    match_id, state["finished"], state["score"])

    # ── schedule + main loop ───────────────────────────────────────────────────
    @staticmethod
    def _next_kickoff(matches: list, now: datetime.datetime):
        """Pick the soonest not-yet-finished match. Returns (id, et_day, kickoff_utc)."""
        from v2.integration.wc_schedule import et_date
        cand = []
        for m in matches:
            ud = m.get("utcDate")
            if not ud or m.get("status") in DONE:
                continue
            try:
                ko = datetime.datetime.fromisoformat(ud.replace("Z", "+00:00")).replace(tzinfo=None)
            except (ValueError, AttributeError):
                continue
            # same MATCH_MAX as _watch's deadline, so a dead match drops out of the
            # candidate window exactly when _watch gives up — never re-watched.
            if ko + MATCH_MAX > now:                    # window not past
                cand.append((m.get("id"), et_date(ud), ko))
        cand.sort(key=lambda x: x[2])
        return cand[0] if cand else None

    async def _loop(self) -> None:
        while self._running:
            try:
                data = await self._get(self.keys[0], f"{BASE_URL}/competitions/WC/matches")
                nxt = self._next_kickoff(data.get("matches", []), _utcnow()) if data else None
                if not nxt:
                    await asyncio.sleep(600)            # nothing upcoming; recheck in 10 min
                    continue
                await self._watch(*nxt)
            except Exception:  # noqa: BLE001 - the scheduling loop must never die
                logger.exception("MatchWatcher loop error")
                await asyncio.sleep(60)

    async def start(self) -> None:
        self._conn = get_connection(self.db_path)
        try:
            row = self._conn.execute(
                "SELECT id FROM organizations WHERE slug=?", (self.org_slug,)).fetchone()
            if row is None:
                raise RuntimeError(f"MatchWatcher: org slug '{self.org_slug}' not found")
            self.org_id = row["id"]
        except Exception:
            self._conn.close()
            self._conn = None
            raise
        self._running = True
        logger.info("MatchWatcher started (keys=%d, channel #%s, org=%s)",
                    len(self.keys), self.channel, self.org_id)
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._conn:
            self._conn.close()
        logger.info("MatchWatcher stopped")
