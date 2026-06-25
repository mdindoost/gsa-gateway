"""EspnMatchWatcher — the live World Cup watcher driven by ESPN (scoreboard-primary).

Subclasses ``MatchWatcher`` to REUSE its proven scheduling / active-set / tick-loop /
posting / retire logic untouched, overriding ONLY the data-source seam:

  * fetch        → ``EspnProvider`` (one shared scoreboard call; no auth key)
  * select       → NormMatch-aware ``_select_active`` (state is already canonical)
  * process      → ``espn_process.process_match`` (goal-identity diff, corrections, shootout)
  * ledger       → set-backed ``announced_goals`` with JSON-safe save/load (review B1)
  * previews     → matchup-only from NormMatch (group table = G7, deferred & flagged)

The football-data ``MatchWatcher`` is left 100% intact, so ``WC_PROVIDER=football_data`` +
restart is a true one-flag kill-switch. The two providers use SEPARATE state files so a
switch never feeds one provider the other's ledger shape.

Blocked-API contract (review S3): when ESPN throttles (429/403), the fetch overrides return
``[]`` and the base tick loop treats an absent match as transient — it keeps the active set
and retires ONLY on finished/MATCH_MAX, never on an empty fetch. So a block costs at most a
quiet tick, never a dropped match or a missed full-time.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path

from v2.integration.match_watcher import MatchWatcher, PRE_KICKOFF_LEAD, MATCH_MAX
from v2.integration.wc_providers.espn import EspnProvider, BlockedError
from v2.integration.wc_providers.espn_process import (
    fresh_ledger as espn_fresh_ledger, process_match)

logger = logging.getLogger(__name__)

DEFAULT_ESPN_STATE_FILE = (
    Path(__file__).resolve().parents[2] / "data" / "match_watcher_espn_state.json")


class EspnMatchWatcher(MatchWatcher):
    def __init__(self, keys, db_path, org_slug="gsa", channel="world-cup-2026",
                 state_file=None, provider=None):
        super().__init__(keys, db_path, org_slug, channel,
                         state_file or DEFAULT_ESPN_STATE_FILE)
        self._espn = provider or EspnProvider()

    # ── ledger: set-backed announced_goals, JSON-safe (review B1) ────────────────
    @staticmethod
    def _fresh_ledger() -> dict:
        d = espn_fresh_ledger()
        d["preview_posted"] = False
        return d

    @staticmethod
    def _normalize(st: dict) -> dict:
        """Coerce a loaded record to the canonical ESPN ledger shape; rebuild the
        ``announced_goals`` set from the list-of-lists the JSON round-trip produced."""
        return {"started": bool(st.get("started", False)),
                "finished": bool(st.get("finished", False)),
                "half": int(st.get("half", 1)),
                "preview_posted": bool(st.get("preview_posted", False)),
                "score": tuple(st.get("score") or (0, 0)),
                "announced_goals": {tuple(g) for g in st.get("announced_goals", [])}}

    def save_states(self) -> None:
        """Atomic write; a Python ``set`` isn't JSON-serializable, so encode
        ``announced_goals`` as a list of identity lists (rebuilt by ``_normalize`` on load)."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            data = {str(mid): {**st, "score": list(st["score"]),
                               "announced_goals": [list(g) for g in st.get("announced_goals", ())]}
                    for mid, st in self._states.items()}
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self.state_file)
        except OSError as exc:
            logger.warning("EspnMatchWatcher: could not save state: %s", exc)

    # ── data-source seam ─────────────────────────────────────────────────────────
    async def _fetch_all(self, key=None):
        """Idle-path discovery: fetch TODAY and TOMORROW's ET day and merge, so a match that
        kicks off just after UTC midnight (on the prior ET day) is never missed at first
        discovery (review #2). Blocked/empty → whatever we got (idle loop rechecks later)."""
        from v2.integration.wc_schedule import et_date
        now = datetime.datetime.now(datetime.timezone.utc)
        days = {et_date(now.isoformat()),
                et_date((now + datetime.timedelta(days=1)).isoformat())}
        seen: dict[int, object] = {}
        for day in sorted(days):
            try:
                for m in await self._espn.fetch_matches(et_day=day):
                    if m.id is not None:
                        seen[m.id] = m
            except BlockedError:
                continue
        return list(seen.values())

    async def _fetch_days(self, et_days, key=None) -> dict[int, object]:
        """Active-path shared fetch: {match_id: NormMatch} for the active ET day(s). ESPN
        groups its scoreboard by ET day, so a single ``?dates=`` query per day catches the
        late spillover games too. Blocked → skip this tick (active set persists)."""
        rows: dict[int, object] = {}
        for et_day in sorted(et_days):
            try:
                matches = await self._espn.fetch_matches(et_day=et_day)
            except BlockedError:
                matches = []
            for m in matches:
                if m.id is not None:
                    rows[m.id] = m
        return rows

    @staticmethod
    def _select_active(matches, now, finished_ids=frozenset()):
        """ALL matches whose watch window is open, sorted by kickoff — NormMatch variant.
        ``state`` is already canonical, so ``done`` is a direct check (no ``_canon``)."""
        from v2.integration.wc_schedule import et_date
        out = []
        for m in matches:
            ud = m.utc_date
            if not ud or m.state == "done" or m.id in finished_ids:
                continue
            try:
                ko = datetime.datetime.fromisoformat(
                    ud.replace("Z", "+00:00")).replace(tzinfo=None)
            except (ValueError, AttributeError):
                continue
            if ko - PRE_KICKOFF_LEAD <= now < ko + MATCH_MAX:
                out.append((m.id, et_date(ud), ko))
        out.sort(key=lambda x: x[2])
        return out

    def _process(self, match, state, near_kickoff: bool = False):
        """Delegate to the ESPN event-driven state machine (goal-identity diff)."""
        return process_match(match, state, near_kickoff)

    async def _post_due_previews(self, rows_by_id) -> None:
        """Matchup-only preview from NormMatch. The live group TABLE (G7) is deferred — ESPN's
        scoreboard carries no group letter; building it from the standings endpoint is the
        flagged follow-up. Until then the preview is matchup + kickoff context, no table."""
        due = [mid for mid in self._active
               if not self._states[mid].get("preview_posted") and rows_by_id.get(mid)]
        if not due:
            return
        for match_id in due:
            nm = rows_by_id[match_id]
            pv = {"homeTeam": {"name": nm.home.name}, "awayTeam": {"name": nm.away.name},
                  "group": nm.group or "", "stage": "", "utcDate": nm.utc_date}
            try:
                if self._post_preview(match_id, pv, []):     # [] → matchup-only
                    self._states[match_id]["preview_posted"] = True
            except Exception:  # noqa: BLE001 - a bad preview must never sink the tick
                logger.exception("EspnMatchWatcher: preview failed for %s", match_id)
        self.save_states()


def make_watcher(keys, db_path, org_slug="gsa", channel="world-cup-2026", state_file=None):
    """Select the live World Cup watcher by ``WC_PROVIDER`` (default ``espn``).
    ``WC_PROVIDER=football_data`` returns the legacy MatchWatcher — the kill-switch."""
    provider = os.getenv("WC_PROVIDER", "espn").strip().lower()
    if provider == "football_data":
        return MatchWatcher(keys, db_path, org_slug, channel, state_file)
    return EspnMatchWatcher(keys, db_path, org_slug, channel, state_file)
