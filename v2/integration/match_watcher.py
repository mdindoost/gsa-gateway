"""MatchWatcher — schedule-driven, active-set World Cup live poller.

Watches EVERY simultaneously-live match at once (the group-finale days run two
games per group in parallel). One shared fetch per tick carries the live state of
every game that day, so concurrency costs no extra API calls — the tick loop fans
that single payload out to a per-match state machine + ledger (both keyed by
match_id, so they never interfere).

  * Active set: a match enters ~5 min before kickoff and leaves when it finishes
    or hits its MATCH_MAX deadline. No API calls between match windows (one cheap
    schedule read per idle sleep).
  * Each tick: ONE shared day fetch -> run every active match's state machine ->
    post events -> persist once. Adaptive cadence: HOT (~2s) right when events are
    likely (a window just opened / a goal or half-resume just fired), COOL (~25s)
    otherwise, keeping the average under the free tier's 10 req/min/key cap.
  * First live read -> "kick-off"; higher score -> the new scoreline (goal);
    FINISHED -> full-time (the FINISHED read may carry no score, so the STORED
    score is the fallback). Score is stored MONOTONICALLY, so a stale/empty read
    can never erase it and a steady cadence is safe.

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
# Note: a match stuck in an uncatchable state mid-window (e.g. SUSPENDED) is intentionally a no-op —
# it may resume — so the tick loop just keeps polling it until its MATCH_MAX deadline. By design.
_CANON = {
    "IN_PLAY": "in_play", "LIVE": "in_play",   # in-play synonyms unify here
    "PAUSED":  "paused",                        # break (half-time) — drives half tracking
    "FINISHED": "done", "AWARDED": "done",      # end-of-match (AWARDED = forfeit/administrative)
}
_CATCHABLE_CANON = {"in_play", "paused", "done"}   # the states _process acts on; rest are ignored


def _canon(status: str | None) -> str | None:
    """football-data raw status → canonical state (in_play / paused / done), or None if the
    watcher doesn't act on it. The one source of truth for in-play-synonym handling."""
    return _CANON.get(status)

PRE_KICKOFF_LEAD = datetime.timedelta(minutes=5)
IDLE_SLEEP = 10 * 60              # no window open → recheck the schedule in 10 min (one cheap
                                  # full-list read per sleep; lookahead ≥ this so a window can't
                                  # open during a sleep we slept past)
DEFAULT_STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "match_watcher_state.json"
# Adaptive shared-fetch cadence (one fetch/tick serves EVERY live match — see the design
# doc). Poll HOT right when events are likely (a window just opened so we're catching the
# real kickoff; or an event fired in the last HOT_WINDOW so follow-up goals cluster), and
# COOL otherwise. The API refreshes a score ~once/min, so COOL still samples each update
# 2–3×; HOT catches kickoffs/goals within ~2s. One shared fetch keeps cost flat regardless
# of how many games are live, and the average stays under the 10 req/min/key free-tier cap.
HOT_INTERVAL = 2
COOL_INTERVAL = 25
HOT_WINDOW = datetime.timedelta(seconds=60)           # stay hot this long after an event
MATCH_MAX = datetime.timedelta(hours=4)               # safety stop after kickoff. Bounds how
# long we keep polling a match we NEVER see finish (a missed/absent "done" read → otherwise
# unbounded polling → ESPN block). A completed match retires immediately on the `finished`
# flag, so this only ever fires on stuck/abandoned matches. Must exceed the longest real
# knockout (120' + shootout + breaks + stoppage + kickoff delay ≈ 3h20m); 4h clears it with
# headroom. (2026-07-12: was 2h30m, which fired on LIVE extra-time/penalty matches — the
# knockout-coverage fix; group-stage matches never exceeded 2h30m so it went unnoticed.)
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
    def __init__(self, keys, ops_path: str, kb_path: str | None = None,
                 org_slug: str = "gsa",
                 channel: str = "world-cup-2026", state_file=None):
        """Two-connection constructor.

        ``ops_path`` — path to the OPS DB; ``enqueue_post`` writes posts here.
        ``kb_path``  — path to the Knowledge DB; org lookup + settings reads
                       (``auto_delete_hours``). Defaults to ``ops_path`` when
                       not provided (behavior-preserving combined-file mode).

        Existing callers that pass a single ``db_path`` continue to work because
        ``kb_path`` defaults to ``ops_path``.
        """
        self.keys = keys if isinstance(keys, list) else \
            [k.strip() for k in (keys or "").split(",") if k.strip()]
        self.ops_path = ops_path
        self.kb_path = kb_path if kb_path is not None else ops_path
        # Back-compat alias: callers that read .db_path still get the ops path
        self.db_path = self.ops_path
        self.org_slug = org_slug
        self.channel = channel
        self.state_file = Path(state_file) if state_file else DEFAULT_STATE_FILE
        self._states: dict[int, dict] = {}   # match_id -> ledger of what we've ANNOUNCED
        self._active: dict[int, dict] = {}   # match_id -> {et_day, kickoff_utc} for OPEN windows
        self._hot_until: dict[int, datetime.datetime] = {}  # match_id -> stay-hot deadline
        self._key_idx = 0                     # round-robin cursor over self.keys
        self._conn = None      # OPS connection (legacy name kept for subclass compat)
        self._kb_conn = None   # Knowledge connection for org/settings reads
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

    def _next_key(self) -> str:
        """Round-robin the configured keys so successive fetches spread load across both
        (the free tier caps 10 req/min PER KEY)."""
        if not self.keys:
            return ""
        key = self.keys[self._key_idx % len(self.keys)]
        self._key_idx += 1
        return key

    async def _fetch_all(self, key: str) -> list[dict]:
        """The whole WC fixture list (schedule + live status/score for every match) in ONE
        call — used to find the next window when idle. [] on any failure."""
        data = await self._get(key, f"{BASE_URL}/competitions/WC/matches")
        return (data or {}).get("matches", [])

    async def _fetch_days(self, et_days, key: str) -> dict[int, dict]:
        """Every match on the given ET-day(s), merged into {match_id: row}. One call per
        distinct day (normally 1; 2 only when a late game spills to the next ET day). This is
        the single shared fetch that feeds EVERY active match's state machine — the reason
        concurrency costs no extra API calls."""
        rows: dict[int, dict] = {}
        for et_day in sorted(et_days):
            nxt = (datetime.date.fromisoformat(et_day) + datetime.timedelta(days=1)).isoformat()
            url = f"{BASE_URL}/competitions/WC/matches?dateFrom={et_day}&dateTo={nxt}"
            data = await self._get(key, url)
            for m in (data or {}).get("matches", []):
                if m.get("id") is not None:
                    rows[m["id"]] = m
                    self._debug(key, m)
        return rows

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

    def _post_preview(self, match_id: int, match: dict | None, group_rows: list) -> bool:
        """Post the one-time pre-match preview (matchup + kickoff/group context + the live
        group table) ~5 min before kickoff. The match row and its group's standings
        ``group_rows`` are INJECTED by the caller — fetched once per tick and shared across
        all simultaneous previews, so N previews never cost N fetches. Gated by
        ``FOOTBALL_PREVIEW_ENABLED`` (default on). Best-effort: returns False (no post) if
        disabled or the match row is unavailable this tick. The persisted ``{id}:preview``
        dedup row is the durable once-per-match guard on top of the ledger flag."""
        if os.getenv("FOOTBALL_PREVIEW_ENABLED", "true").lower() == "false":
            return False
        if not match:
            return False
        content = build_match_preview(match, group_rows, _kickoff_et(match.get("utcDate", "")))
        try:
            enqueue_post(self._conn, self._kb_conn or self._conn, PostDraft(
                org_id=self.org_id, content=content, type="worldcup",
                channels=platform_channels(), discord_channel=self.channel,
                source_type="worldcup", dedup_key=f"{match_id}:preview",
                delete_at=self._wc_delete_at(),
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
        # ESPN events carry a goal-identity `uid` (athlete+minute) — unique per goal, so a
        # re-scored goal after a disallowance never collides on score alone (posts are
        # immortal; a collision drops the 2nd forever). football-data events have no uid and
        # fall through to the score+gen scheme below.
        if ev.get("uid"):
            return f"{match_id}:{ev['type']}:{ev['uid']}"
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

    def _wc_delete_at(self) -> str:
        """WorldCup posts flood the channel, so they auto-delete after the configured window
        (default.auto_delete_hours, default 24, clamped 1..48). Absolute UTC delete_at.
        Reads settings from the Knowledge DB (self._kb_conn)."""
        from v2.core.publishing.sources import auto_delete_hours
        # Use KB conn for settings reads; fall back to OPS conn in combined-file mode
        kb = self._kb_conn if self._kb_conn is not None else self._conn
        hours = auto_delete_hours(kb, self.org_id)
        return (_utcnow() + datetime.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

    def _post(self, match_id: int, ev: dict) -> None:
        try:
            # OPS conn for enqueue; KB conn was used at start() to resolve org_id
            enqueue_post(self._conn, self._kb_conn or self._conn, PostDraft(
                org_id=self.org_id, content=format_event(ev), type="worldcup",
                channels=platform_channels(), discord_channel=self.channel,
                source_type="worldcup", dedup_key=self._dedup_key(match_id, ev),
                delete_at=self._wc_delete_at(),
                metadata={"event_type": ev["type"]}))
            logger.info("MatchWatcher: posted %s for match %s", ev["type"], match_id)
        except EnqueueError as exc:
            logger.warning("MatchWatcher: dropped %s: %s", ev.get("type"), exc)

    # ── tick fan-out (one shared payload → every active match) ──────────────────
    def _collect_tick_events(self, rows_by_id: dict[int, dict],
                             now: datetime.datetime) -> list[tuple[int, dict]]:
        """Run every active match's state machine against the shared payload and return
        the (match_id, event) pairs to post. Each match is processed in ISOLATION — a
        malformed row or a raising ``_process`` for one match logs and is skipped, never
        aborting the tick for the others. A match absent from this tick's payload is
        skipped (transient — a later tick catches up). Producing any event marks the match
        HOT so the cadence tightens to catch the follow-up."""
        out: list[tuple[int, dict]] = []
        for match_id, info in list(self._active.items()):
            row = rows_by_id.get(match_id)
            if row is None:
                continue
            near = now < info["kickoff_utc"] + KICKOFF_GRACE
            try:
                events = self._process(row, self._states[match_id], near)
            except Exception:  # noqa: BLE001 - one bad match must not sink the whole tick
                logger.exception("MatchWatcher: _process failed for match %s", match_id)
                continue
            if events:
                self._hot_until[match_id] = now + HOT_WINDOW
            for ev in events:
                out.append((match_id, ev))
        return out

    def _poll_interval(self, now: datetime.datetime) -> int:
        """HOT cadence when any active match is awaiting kickoff (not yet started) or had an
        event within HOT_WINDOW; COOL otherwise (incl. no active matches)."""
        for match_id, info in self._active.items():
            if not self._states.get(match_id, {}).get("started"):
                # Poll HOT to catch the real kickoff — but only until KICKOFF_GRACE past the
                # scheduled time. A postponed/never-started match must not HOT-spin (2s) for the
                # whole (now 4h) window on the block-prone ESPN endpoint; past the grace it falls
                # through to COOL like any other quiet match.
                if now < info["kickoff_utc"] + KICKOFF_GRACE:
                    return HOT_INTERVAL
                continue
            hot_until = self._hot_until.get(match_id)
            if hot_until is not None and now < hot_until:
                return HOT_INTERVAL                     # recent event → follow-ups likely
        return COOL_INTERVAL

    # ── schedule + main loop ───────────────────────────────────────────────────
    @staticmethod
    def _select_active(matches: list, now: datetime.datetime, finished_ids=frozenset()):
        """ALL matches whose watch window is currently open, sorted by kickoff.

        Returns a list of (id, et_day, kickoff_utc). A window is OPEN from
        ``kickoff - PRE_KICKOFF_LEAD`` (so the preview + kickoff-catch fire) until
        ``kickoff + MATCH_MAX`` (the same safety deadline a watch gives up at). This is
        the concurrent replacement for ``_next_kickoff`` — every simultaneous game is
        returned, not just the soonest. ``finished_ids`` (matches our ledger has wrapped
        up) are skipped even if the stale API still reports them live."""
        from v2.integration.wc_schedule import et_date
        out = []
        for m in matches:
            ud = m.get("utcDate")
            if not ud or _canon(m.get("status")) == "done" or m.get("id") in finished_ids:
                continue
            try:
                ko = datetime.datetime.fromisoformat(ud.replace("Z", "+00:00")).replace(tzinfo=None)
            except (ValueError, AttributeError):
                continue
            if ko - PRE_KICKOFF_LEAD <= now < ko + MATCH_MAX:   # window open
                out.append((m.get("id"), et_date(ud), ko))
        out.sort(key=lambda x: x[2])
        return out

    def _finished_ids(self) -> set:
        return {mid for mid, st in self._states.items() if st.get("finished")}

    def _register_active(self, selected) -> None:
        """Add any newly-open match to the active set (resuming/creating its ledger). Never
        removes — a match that has just finished must stay active for THIS tick so its
        full-time event posts; ``_retire_active`` drops it afterwards."""
        for match_id, et_day, kickoff_utc in selected:
            if match_id not in self._active:
                self._active[match_id] = {"et_day": et_day, "kickoff_utc": kickoff_utc}
                self._match_state(match_id)            # resume persisted ledger or start fresh

    def _retire_active(self, now: datetime.datetime) -> None:
        """Drop matches whose ledger is finished or whose window has passed (restart-safe: an
        unfinished-but-expired ledger is retired, never re-watched)."""
        for match_id in list(self._active):
            kickoff_utc = self._active[match_id]["kickoff_utc"]
            if self._states.get(match_id, {}).get("finished") or now >= kickoff_utc + MATCH_MAX:
                del self._active[match_id]
                self._hot_until.pop(match_id, None)

    async def _post_due_previews(self, rows_by_id: dict[int, dict]) -> None:
        """Post the one-time preview for every active match that needs one, fetching the
        standings table ONCE and sharing it across all of them (so N simultaneous previews
        cost ~1 standings call, not N)."""
        due = [mid for mid in self._active
               if not self._states[mid].get("preview_posted") and rows_by_id.get(mid)]
        if not due:
            return
        standings = await self._fetch_standings(self._next_key())
        for match_id in due:
            match = rows_by_id[match_id]
            group_rows = standings.get(match.get("group") or "", [])
            try:
                if self._post_preview(match_id, match, group_rows):
                    self._states[match_id]["preview_posted"] = True
            except Exception:  # noqa: BLE001 - a bad preview must never sink the tick
                logger.exception("MatchWatcher: preview failed for %s", match_id)
        self.save_states()

    async def _tick_once(self) -> float:
        """One iteration of the watch loop. Returns the seconds to sleep before the next tick
        (kept separate from the sleep so it's directly testable). When idle, one cheap full-list
        read finds the next window; when matches are active, ONE shared day fetch feeds every
        one of them. Returns IDLE_SLEEP when nothing is open, else the adaptive poll interval."""
        now = _utcnow()
        if not self._active:                            # idle → find the next window cheaply
            self._register_active(self._select_active(await self._fetch_all(self._next_key()),
                                                      now, self._finished_ids()))
            if not self._active:
                return IDLE_SLEEP
        et_days = {info["et_day"] for info in self._active.values()}
        rows = await self._fetch_days(et_days, self._next_key())   # the one shared fetch
        # Same-day matches entering their window are picked up from this very payload.
        self._register_active(self._select_active(list(rows.values()), now, self._finished_ids()))
        await self._post_due_previews(rows)
        for match_id, ev in self._collect_tick_events(rows, now):
            self._post(match_id, ev)
        self.save_states()
        self._retire_active(now)                        # drop finished / expired AFTER posting
        return self._poll_interval(now)

    async def _loop(self) -> None:
        while self._running:
            try:
                delay = await self._tick_once()
            except Exception:  # noqa: BLE001 - the scheduling loop must never die
                logger.exception("MatchWatcher loop error")
                delay = 60
            await asyncio.sleep(delay)

    async def start(self) -> None:
        self.load_states()        # resume any match that was in progress at shutdown
        from v2.core.database.schema import get_ops_connection
        from v2.core.publishing.org_resolve import resolve_org
        # Both connections opened inside the try so neither leaks on failure (F7).
        try:
            self._conn = get_ops_connection(self.ops_path)   # OPS: post writes
            self._kb_conn = get_connection(self.kb_path)     # KB: org lookup + settings
            # resolve_org fails loudly on >1 match (LOW-11) so slug collisions
            # are caught before any posts are enqueued (F6).
            org_row = resolve_org(self._kb_conn, self.org_slug)
            self.org_id = org_row["id"]
        except Exception:
            if self._conn:
                self._conn.close()
                self._conn = None
            if self._kb_conn:
                self._kb_conn.close()
                self._kb_conn = None
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
        if self._kb_conn:
            self._kb_conn.close()
        logger.info("MatchWatcher stopped")
