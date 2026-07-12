"""EspnMatchWatcher wiring — ledger set/JSON round-trip (B1), NormMatch scheduling, factory.

The subclass reuses MatchWatcher's scheduling/tick/posting and overrides only the data
source. These tests pin the integration hazards the senior-eng review flagged.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.integration.match_watcher import (
    MatchWatcher, MATCH_MAX, KICKOFF_GRACE, HOT_INTERVAL, COOL_INTERVAL)
from v2.integration.wc_providers.watcher import EspnMatchWatcher, make_watcher
from v2.integration.wc_providers.normalize import NormMatch, TeamRef


def nm(mid, ud, state, score=(0, 0)):
    return NormMatch(id=mid, utc_date=ud, state=state,
                     home=TeamRef(1, "A"), away=TeamRef(2, "B"), score=score)


def test_fresh_ledger_has_set_and_preview_flag():
    led = EspnMatchWatcher._fresh_ledger()
    assert isinstance(led["announced_goals"], set)
    assert led["preview_posted"] is False


def test_ledger_with_goals_round_trips_through_save_load(tmp_path):
    # B1: announced_goals is a set of identity tuples — must survive the JSON save/load that
    # runs every tick (a raw set raises TypeError in json.dumps).
    w = EspnMatchWatcher([], ":memory:", state_file=tmp_path / "st.json")
    w._states = {760462: {"started": True, "finished": False, "half": 1,
                          "preview_posted": True, "score": (1, 0),
                          "announced_goals": {(760462, 391034, "29'"),
                                              (760462, "seq1", "34'")}}}
    w.save_states()                                   # must not raise
    w2 = EspnMatchWatcher([], ":memory:", state_file=tmp_path / "st.json")
    w2.load_states()
    led = w2._states[760462]
    assert isinstance(led["announced_goals"], set)
    assert (760462, 391034, "29'") in led["announced_goals"]
    assert (760462, "seq1", "34'") in led["announced_goals"]
    assert led["score"] == (1, 0)


def test_select_active_works_on_normmatch():
    # a match whose window is open (kickoff just now) is selected; a finished one is not.
    now = datetime.datetime(2026, 6, 25, 1, 2, 0)
    matches = [nm(1, "2026-06-25T01:00Z", "in_play"),
               nm(2, "2026-06-25T01:00Z", "done")]
    sel = EspnMatchWatcher._select_active(matches, now)
    ids = [s[0] for s in sel]
    assert 1 in ids and 2 not in ids


def test_process_dispatches_to_espn_state_machine():
    w = EspnMatchWatcher([], ":memory:")
    led = EspnMatchWatcher._fresh_ledger()
    evs = w._process(nm(1, "2026-06-25T01:00Z", "in_play", (0, 0)), led)
    assert [e["type"] for e in evs] == ["kickoff"]


class _RecordingProvider:
    def __init__(self):
        self.days = []
    async def fetch_matches(self, et_day=None):
        self.days.append(et_day)
        return []


def test_idle_fetch_all_queries_today_and_tomorrow_et():
    # Review #2: idle discovery must span the ET-midnight boundary so a match that kicks off
    # just after UTC midnight (prior ET day) isn't missed. Expect two distinct ET days queried.
    import asyncio
    prov = _RecordingProvider()
    w = EspnMatchWatcher([], ":memory:", provider=prov)
    asyncio.new_event_loop().run_until_complete(w._fetch_all())
    assert len(set(prov.days)) == 2          # today + tomorrow ET
    assert all(d is not None for d in prov.days)


def test_factory_selects_provider_by_flag(monkeypatch):
    monkeypatch.setenv("WC_PROVIDER", "espn")
    assert isinstance(make_watcher("k", "db", "gsa", "chan"), EspnMatchWatcher)
    monkeypatch.setenv("WC_PROVIDER", "football_data")
    w = make_watcher("k", "db", "gsa", "chan")
    assert isinstance(w, MatchWatcher) and not isinstance(w, EspnMatchWatcher)


def test_factory_defaults_to_espn(monkeypatch):
    monkeypatch.delenv("WC_PROVIDER", raising=False)
    assert isinstance(make_watcher("k", "db", "gsa", "chan"), EspnMatchWatcher)


# ── knockout timing: MATCH_MAX widened to cover extra time / penalties ────────
def _watcher_with_active(tmp_path, mid, ko, started, finished=False):
    w = EspnMatchWatcher([], ":memory:", state_file=tmp_path / "st.json")
    w._active = {mid: {"et_day": "2026-07-11", "kickoff_utc": ko}}
    w._states = {mid: {"started": started, "finished": finished, "score": (1, 1),
                       "announced_goals": set(), "half": 1, "preview_posted": True}}
    return w


def test_match_max_covers_overtime_and_shootout():
    # a knockout (120' + shootout ≈ 3h20m) must outlive the old 2h30m cap.
    assert MATCH_MAX >= datetime.timedelta(hours=3, minutes=30)


def test_live_match_not_retired_past_old_cap_but_dropped_at_new_cap(tmp_path):
    ko = datetime.datetime(2026, 7, 11, 21, 1, 0)
    w = _watcher_with_active(tmp_path, 760513, ko, started=True)
    w._retire_active(ko + datetime.timedelta(hours=2, minutes=35))   # past OLD 2h30m
    assert 760513 in w._active                                        # still watched (in ET)
    w._retire_active(ko + MATCH_MAX + datetime.timedelta(minutes=1))  # past new cap
    assert 760513 not in w._active


def test_finished_match_retired_immediately_regardless_of_cap(tmp_path):
    ko = datetime.datetime(2026, 7, 11, 21, 1, 0)
    w = _watcher_with_active(tmp_path, 760513, ko, started=True, finished=True)
    w._retire_active(ko + datetime.timedelta(minutes=10))             # only 10' in
    assert 760513 not in w._active                                    # dropped on `finished`


def test_never_started_match_hot_only_within_grace_then_cools(tmp_path):
    # a postponed/never-started match must not HOT-spin (2s) for the whole 4h window.
    ko = datetime.datetime(2026, 7, 11, 21, 1, 0)
    w = _watcher_with_active(tmp_path, 760513, ko, started=False)
    assert w._poll_interval(ko + datetime.timedelta(minutes=10)) == HOT_INTERVAL
    assert w._poll_interval(ko + KICKOFF_GRACE + datetime.timedelta(minutes=1)) == COOL_INTERVAL
