"""Tests for MatchWatcher._process — the live-score state machine.

Regression: a mid-game restart reset the in-memory state to score=(0,0), so the
first read of an in-progress match (e.g. 1-1) was treated as a jump from 0-0 and
the "walk goals up" logic synthesized a phantom "1-0" (home-first) plus a
duplicate kickoff. Fix: the first read of an already-in-progress match adopts the
current score as a silent baseline — no kickoff, no back-announced goals.
"""

from v2.integration.match_watcher import MatchWatcher


def _live(h, a, home="Brazil", away="Morocco"):
    return {"status": "IN_PLAY", "homeTeam": {"name": home}, "awayTeam": {"name": away},
            "score": {"fullTime": {"home": h, "away": a}}}


def _fresh():
    return {"started": False, "score": (0, 0), "finished": False}


def _w():
    return MatchWatcher([], ":memory:")   # __init__ opens nothing


def _types(events):
    return [e["type"] for e in events]


def test_join_in_progress_match_baselines_silently():
    st = _fresh()
    events = _w()._process(_live(1, 1), st)
    assert events == []                      # no phantom 1-0, no duplicate kickoff
    assert st["started"] and st["score"] == (1, 1)


def test_goals_after_joining_are_announced_from_the_baseline():
    w, st = _w(), _fresh()
    w._process(_live(1, 1), st)              # joined mid-game at 1-1
    ev = w._process(_live(2, 1), st)         # Brazil scores -> 2-1
    assert _types(ev) == ["goal"]
    assert ev[0]["match"]["score"]["fullTime"] == {"home": 2, "away": 1}


def test_kickoff_still_fires_when_started_at_0_0():
    st = _fresh()
    ev = _w()._process(_live(0, 0), st)
    assert _types(ev) == ["kickoff"]
    assert st["score"] == (0, 0)


def test_in_game_progression_keeps_real_order():
    # Caught from kickoff: away scores first (0-1), then home equalizes (1-1).
    w, st = _w(), _fresh()
    w._process(_live(0, 0), st)              # kickoff
    ev1 = w._process(_live(0, 1), st)
    assert ev1[0]["match"]["score"]["fullTime"] == {"home": 0, "away": 1}
    ev2 = w._process(_live(1, 1), st)
    assert ev2[0]["match"]["score"]["fullTime"] == {"home": 1, "away": 1}
