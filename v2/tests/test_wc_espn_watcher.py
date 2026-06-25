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

from v2.integration.match_watcher import MatchWatcher
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


def test_factory_selects_provider_by_flag(monkeypatch):
    monkeypatch.setenv("WC_PROVIDER", "espn")
    assert isinstance(make_watcher("k", "db", "gsa", "chan"), EspnMatchWatcher)
    monkeypatch.setenv("WC_PROVIDER", "football_data")
    w = make_watcher("k", "db", "gsa", "chan")
    assert isinstance(w, MatchWatcher) and not isinstance(w, EspnMatchWatcher)


def test_factory_defaults_to_espn(monkeypatch):
    monkeypatch.delenv("WC_PROVIDER", raising=False)
    assert isinstance(make_watcher("k", "db", "gsa", "chan"), EspnMatchWatcher)
