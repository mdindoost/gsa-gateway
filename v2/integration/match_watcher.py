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
import json
import logging
import os
from pathlib import Path

import aiohttp

from v2.core.database.schema import get_connection
from v2.core.publishing.sources import EnqueueError, PostDraft, enqueue_post, platform_channels
from v2.integration.worldcup_tracker import BASE_URL, DEBUG_FILE, format_event
from v2.integration.match_preview import build_match_preview
from v2.integration.daily_fixtures import _kickoff_et

logger = logging.getLogger(__name__)

# Canonical match states — the SINGLE place that maps football-data's raw status strings to the
# states the watcher acts on. football-data reports an in-progress match as EITHER "IN_PLAY" or
# "LIVE" (varies per match: England v Ghana 2026-06-23 used "LIVE", Portugal v Uzbekistan used
# "IN_PLAY"); both normalize to "in_play". Adding a future synonym = one entry here. Any status NOT
# in the map (SCHEDULED/TIMED/SUSPENDED/POSTPONED/CANCELLED/unknown) → None → uncatchable, ignored.
_CANON = {
    "IN_PLAY": "in_play", "LIVE": "in_play",   # in-play synonyms unify here
    "PAUSED":  "paused",                        # break (half-time) — drives half tracking
    "FINISHED": "done", "AWARDED": "done",      # end-of-match (AWARDED = forfeit/administrative)
}
_CATCHABLE_CANON = {"in_play", "paused", "done"}   # states _catch() returns; rest are ignored


def _canon(status: str | None) -> str | None:
    """football-data raw status → canonical state (in_play / paused / done), or None if the
    watcher doesn't act on it. The one source of truth for in-play-synonym handling."""
    return _CANON.get(status)

PRE_KICKOFF_LEAD = datetime.timedelta(minutes=5)
REST_SECONDS = 1 * 60             # rest after a successful catch — matches the API's ~1-min
                                  # score-refresh cadence; ≤1 min lag, ~1 read/min (10% of cap)
PRIMARY_TRIES = 6                 # primary-key reads (~1 min at 10s) before bursting
PRIMARY_INTERVAL = 10
BURST_TRIES = 12                  # rapid reads across all keys
BURST_INTERVAL = 2
DEFAULT_STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "match_watcher_state.json"
MATCH_MAX = datetime.timedelta(hours=2, minutes=30)   # safety stop after kickoff
KICKOFF_GRACE = datetime.timedelta(minutes=30)        # if the first live read is caught this
                                                      # soon after the scheduled kickoff it's
                                                      # still "the start" (the free API can
                                                      # report the live state late); catching
                                                      # it later means a mid-match restart


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _half_label(half: int) -> str:
    """Map the running half number to a goal-line label. 3+ is beyond regulation
    (knockout extra time) — a safe catch-all until we observe a real ET match's API."""
    return {1: "First Half", 2: "Second Half"}.get(half, "Extra Time")


class MatchWatcher:
    def __init__(self, keys, db_path: str, org_slug: str = "gsa",
                 channel: str = "world-cup-2026", state_file=None):
        self.keys = keys if isinstance(keys, list) else \
            [k.strip() for k in (keys or "").split(",") if k.strip()]
        self.db_path = db_path
        self.org_slug = org_slug
        self.channel = channel
        self.state_file = Path(state_file) if state_file else DEFAULT_STATE_FILE
        self._states: dict[int, dict] = {}   # match_id -> ledger of what we've ANNOUNCED
        self._conn = None
        self.org_id = None
        self._task = None
        self._running = False
        self.debug_log = os.getenv("FOOTBALL_DEBUG_LOG", "false").lower() == "true"

    # ── ledger persistence (the JSON book-keeping; the API is the truth) ─────────
    @staticmethod
    def _fresh_ledger() -> dict:
        return {"started": False, "score": (0, 0), "finished": False,
                "half": 1, "pending_half": False,
                "score_updated": None, "correction_gen": 0,
                "preview_posted": False}

    @staticmethod
    def _normalize(st: dict) -> dict:
        """Coerce a loaded record to the canonical shape (score→tuple, fill new keys)
        so a file written by an older build never KeyErrors."""
        return {"started": bool(st.get("started", False)),
                "score": tuple(st.get("score") or (0, 0)),
                "finished": bool(st.get("finished", False)),
                "half": int(st.get("half", 1)),
                "pending_half": bool(st.get("pending_half", False)),
                # score_updated: the API `lastUpdated` of the read that set the current
                # score — used to tell a genuine downward correction (VAR / disallowed goal)
                # from a stale/empty read. None on an older file → corrections stay disabled
                # until a real scoring read stamps it.
                "score_updated": st.get("score_updated"),
                # correction_gen: bumped on each downward correction so a re-scored line
                # (0-1 disallowed → 0-0 → 0-1 again) gets a fresh goal dedup key.
                "correction_gen": int(st.get("correction_gen", 0)),
                # preview_posted: the pre-match preview (matchup + group table) fired once.
                "preview_posted": bool(st.get("preview_posted", False))}

    def _match_state(self, match_id: int) -> dict:
        """Resume the persisted ledger for a match, or register a fresh one. The same
        dict is kept in ``self._states`` so a later ``save_states`` persists its mutations."""
        st = self._states.get(match_id)
        if st is None:
            st = self._fresh_ledger()
            self._states[match_id] = st
        return st

    def load_states(self) -> None:
        if not self.state_file.exists():
            return
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            # Drop already-finished matches — they never need resuming, and keeping them
            # would feed the scheduler's "instant return" path (and grow the file unbounded).
            self._states = {int(mid): n for mid, st in raw.items()
                            if not (n := self._normalize(st))["finished"]}
        except (json.JSONDecodeError, ValueError, TypeError, OSError) as exc:
            logger.warning("MatchWatcher: could not load state (%s); starting fresh", exc)
            self._states = {}

    def save_states(self) -> None:
        """Atomic write (temp + rename) so a crash mid-write can't corrupt the ledger."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            data = {str(mid): {**st, "score": list(st["score"])}
                    for mid, st in self._states.items()}
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self.state_file)
        except OSError as exc:
            logger.warning("MatchWatcher: could not save state: %s", exc)

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

    async def _fetch_standings(self, key: str) -> dict[str, list[dict]]:
        """{group_token: [table_rows]} for the WC group stage, keyed in the MATCHES
        format ('Group H' -> 'GROUP_H') so a lookup by ``match['group']`` resolves.
        {} on any failure (never raises)."""
        data = await self._get(key, f"{BASE_URL}/competitions/WC/standings")
        out: dict[str, list[dict]] = {}
        for block in (data or {}).get("standings", []):
            g = block.get("group")
            if g:
                out[g.upper().replace(" ", "_")] = block.get("table", [])
        return out

    async def _post_preview(self, match_id: int, et_day: str) -> bool:
        """Post the one-time pre-match preview (matchup + kickoff/group context + the
        live group table) ~5 min before kickoff. Gated by ``FOOTBALL_PREVIEW_ENABLED``
        (default on). Best-effort: returns False (no post) if disabled or the schedule
        read is unavailable. The persisted ``{id}:preview`` dedup row is the durable
        once-per-match guard on top of the ledger flag."""
        if os.getenv("FOOTBALL_PREVIEW_ENABLED", "true").lower() == "false":
            return False
        match = await self._fetch_match(self.keys[0], match_id, et_day)
        if not match:
            return False
        rows = (await self._fetch_standings(self.keys[0])).get(match.get("group") or "", [])
        content = build_match_preview(match, rows, _kickoff_et(match.get("utcDate", "")))
        try:
            enqueue_post(self._conn, PostDraft(
                org_id=self.org_id, content=content, type="worldcup",
                channels=platform_channels(), discord_channel=self.channel,
                source_type="worldcup", dedup_key=f"{match_id}:preview",
                metadata={"event_type": "preview"}))
            logger.info("MatchWatcher: posted preview for match %s", match_id)
            return True
        except EnqueueError as exc:
            logger.warning("MatchWatcher: dropped preview: %s", exc)
            return False

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
    def _read_meta(match: dict):
        """Return (last_updated, carried_score). ``carried_score`` is True ONLY when the
        payload actually reported both score sides — an empty payload (home/away None)
        must never be treated as a real 0-0 that could lower a tracked score."""
        ft = (match.get("score") or {}).get("fullTime") or {}
        carried = ft.get("home") is not None and ft.get("away") is not None
        return match.get("lastUpdated"), carried

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
        canon = _canon(status)
        read_lu, carried = self._read_meta(match)
        score_lu = state.get("score_updated")
        events: list[dict] = []
        if canon == "done":
            if not state["finished"]:
                state["finished"] = True
                # The FINISHED payload OFTEN carries the true final score — the free API can
                # lag during play. If it's a FRESH read that carried a real score, TRUST it
                # outright (up OR down): up covers "we only ever saw 1-0 live but the match
                # ended 4-1"; down covers a VAR/disallowed goal we caught only at full-time
                # (the live PAUSED corrections were missed). Otherwise (empty/stale FINISHED)
                # fall back to the per-side MAX so a junk 0-0 can't erase our tracked score.
                if carried and read_lu is not None and score_lu is not None and read_lu > score_lu:
                    final = (h, a)
                else:
                    final = (max(h, state["score"][0]), max(a, state["score"][1]))
                state["score"] = final
                events.append({"type": "fulltime",
                               "match": self._with_score(match, final)})
            return events
        if canon in ("in_play", "paused"):
            # Half tracking — derived purely from the PAUSED→IN_PLAY transitions (the free
            # tier's `minute` is unreliable, so we never trust it). PAUSED is a break, so the
            # first IN_PLAY read AFTER one advances the half; goals revealed AT a PAUSED read
            # still belong to the half just played. Flag-gated, so a missed read can't corrupt
            # it. Each goal stamps the current half — we no longer post a separate half-time msg.
            state.setdefault("half", 1)
            state.setdefault("pending_half", False)
            if canon == "paused":
                state["pending_half"] = True
            elif state["pending_half"]:
                state["half"] += 1
                state["pending_half"] = False
            half_label = _half_label(state["half"])

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
            # Downward correction (VAR / disallowed goal): a FRESH read (lastUpdated strictly
            # newer than the stamp on our current score) that CARRIED a real score and is lower
            # on either side is a genuine retraction — update down and announce it. A stale/empty
            # read can only carry an OLD or absent lastUpdated, so it stays under the monotonic
            # guard below and can never lower the score. score_updated=None (no prior stamp)
            # disables corrections — we only trust a drop relative to a known-fresh baseline.
            if (carried and read_lu is not None and score_lu is not None
                    and read_lu > score_lu and (h < ph or a < pa)):
                state["score"] = (h, a)
                state["score_updated"] = read_lu
                state["correction_gen"] = state.get("correction_gen", 0) + 1
                events.append({"type": "correction", "half_label": half_label,
                               "gen": state["correction_gen"],
                               "match": self._with_score(match, (h, a))})
                return events
            nh, na = max(h, ph), max(a, pa)          # monotonic — never go down
            if (nh, na) != (ph, pa):
                # Walk the score up one goal at a time so each goal post shows its
                # own running scoreline AND gets a distinct dedup key (a single
                # read jumping 0-0→2-0 must produce "1-0" then "2-0", not two "2-0"
                # that would collide and drop the 2nd goal). The correction generation
                # is stamped on each goal so a re-scored line after a retraction gets a
                # fresh key instead of colliding with the disallowed goal's.
                gen = state.get("correction_gen", 0)
                cur_h, cur_a = ph, pa
                for _ in range(nh - ph):
                    cur_h += 1
                    events.append({"type": "goal", "scoring_team": match["homeTeam"],
                                   "half_label": half_label, "gen": gen,
                                   "match": self._with_score(match, (cur_h, cur_a))})
                for _ in range(na - pa):
                    cur_a += 1
                    events.append({"type": "goal", "scoring_team": match["awayTeam"],
                                   "half_label": half_label, "gen": gen,
                                   "match": self._with_score(match, (cur_h, cur_a))})
                state["score"] = (nh, na)
                if read_lu is not None:               # stamp the freshness of this score
                    state["score_updated"] = read_lu
        return events

    @staticmethod
    def _dedup_key(match_id: int, ev: dict) -> str:
        if ev["type"] == "goal":
            s = ev["match"]["score"]["fullTime"]
            gen = ev.get("gen", 0)
            # gen omitted when 0 so pre-correction keys stay "<id>:goal:H-A"; after a
            # correction the gen distinguishes a re-scored line from the disallowed one.
            stem = f"goal:{gen}:" if gen else "goal:"
            return f"{match_id}:{stem}{s['home']}-{s['away']}"
        if ev["type"] == "correction":
            s = ev["match"]["score"]["fullTime"]
            gen = ev.get("gen", 0)
            # gen keeps a 2nd correction that lands on the same scoreline (0-0 → goal →
            # disallowed → 0-0 again) from colliding with the first and being dropped.
            stem = f"correction:{gen}:" if gen else "correction:"
            return f"{match_id}:{stem}{s['home']}-{s['away']}"
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
            if m and _canon(m.get("status")) in _CATCHABLE_CANON:
                return m
            await asyncio.sleep(PRIMARY_INTERVAL)
        for i in range(BURST_TRIES):                  # backup key used only here
            if not self._running:
                return None
            m = await self._fetch_match(self.keys[i % len(self.keys)], match_id, et_day)
            if m and _canon(m.get("status")) in _CATCHABLE_CANON:
                return m
            await asyncio.sleep(BURST_INTERVAL)
        return None

    # ── per-match loop ─────────────────────────────────────────────────────────
    async def _watch(self, match_id: int, et_day: str, kickoff_utc: datetime.datetime) -> None:
        wait = (kickoff_utc - PRE_KICKOFF_LEAD - _utcnow()).total_seconds()
        if wait > 0:
            logger.info("MatchWatcher: sleeping %.0fs until match %s window", wait, match_id)
            await asyncio.sleep(wait)
        state = self._match_state(match_id)   # resume the ledger if we were mid-match
        # Pre-match preview (matchup + group table), once, ~5 min before kickoff.
        if not state.get("preview_posted"):
            try:
                if await self._post_preview(match_id, et_day):
                    state["preview_posted"] = True
                    self.save_states()
            except Exception:  # noqa: BLE001 - a bad preview must never block the watch
                logger.exception("MatchWatcher: preview failed for %s", match_id)
        deadline = kickoff_utc + MATCH_MAX
        logger.info("MatchWatcher: watching match %s (resume score=%s half=%s)",
                    match_id, state["score"], state["half"])
        while self._running and not state["finished"] and _utcnow() < deadline:
            m = await self._catch(match_id, et_day)
            if m:
                near = _utcnow() < kickoff_utc + KICKOFF_GRACE
                for ev in self._process(m, state, near):
                    self._post(match_id, ev)
                self.save_states()                     # persist the ledger every catch
                if state["finished"]:
                    break
                await asyncio.sleep(REST_SECONDS)      # caught one → rest, then re-read
        logger.info("MatchWatcher: match %s done (finished=%s score=%s)",
                    match_id, state["finished"], state["score"])

    # ── schedule + main loop ───────────────────────────────────────────────────
    @staticmethod
    def _next_kickoff(matches: list, now: datetime.datetime, finished_ids=frozenset()):
        """Pick the soonest not-yet-finished match. Returns (id, et_day, kickoff_utc).

        ``finished_ids`` are matches we've ALREADY wrapped up in our ledger — skip them even
        if the (stale) API still reports them live, or _watch would instant-return and spin."""
        from v2.integration.wc_schedule import et_date
        cand = []
        for m in matches:
            ud = m.get("utcDate")
            if not ud or _canon(m.get("status")) == "done" or m.get("id") in finished_ids:
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
                fin = {mid for mid, st in self._states.items() if st["finished"]}
                nxt = self._next_kickoff(data.get("matches", []), _utcnow(), fin) if data else None
                if not nxt:
                    await asyncio.sleep(600)            # nothing upcoming; recheck in 10 min
                    continue
                await self._watch(*nxt)
            except Exception:  # noqa: BLE001 - the scheduling loop must never die
                logger.exception("MatchWatcher loop error")
                await asyncio.sleep(60)

    async def start(self) -> None:
        self.load_states()        # resume any match that was in progress at shutdown
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
