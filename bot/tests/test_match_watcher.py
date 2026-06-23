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


# ── The feed reports SOME matches with status="LIVE" instead of "IN_PLAY"
#    (England v Ghana, 2026-06-23). "LIVE" must be treated exactly like IN_PLAY. ──
def _live_str(h, a, status="LIVE", home="England", away="Ghana"):
    return {"status": status, "homeTeam": {"name": home}, "awayTeam": {"name": away},
            "score": {"fullTime": {"home": h, "away": a}}}


def test_live_status_is_catchable():
    from v2.integration.match_watcher import _canon, _CATCHABLE_CANON
    assert _canon("LIVE") == "in_play"          # recognized as in-play (synonym of IN_PLAY)
    assert _canon("LIVE") in _CATCHABLE_CANON   # so _catch() returns the read instead of discarding it


def test_canon_normalizes_all_known_statuses():
    from v2.integration.match_watcher import _canon
    assert _canon("IN_PLAY") == "in_play"
    assert _canon("LIVE") == "in_play"          # the two in-play synonyms unify here
    assert _canon("PAUSED") == "paused"
    assert _canon("FINISHED") == "done"
    assert _canon("AWARDED") == "done"          # forfeit/administrative result → end-of-match
    # uncatchable (unchanged behavior): not in-play, not done → ignored by _catch
    for s in ("SCHEDULED", "TIMED", "SUSPENDED", "POSTPONED", "CANCELLED", "WHATEVER"):
        assert _canon(s) is None


def test_awarded_status_posts_fulltime():
    st = _fresh()
    m = {"status": "AWARDED", "homeTeam": {"name": "A"}, "awayTeam": {"name": "B"},
         "score": {"fullTime": {"home": 3, "away": 0}}}
    ev = _w()._process(m, st)
    assert _types(ev) == ["fulltime"]
    assert st["finished"]
    assert ev[0]["match"]["score"]["fullTime"] == {"home": 3, "away": 0}


def test_live_status_fires_kickoff():
    st = _fresh()
    ev = _w()._process(_live_str(0, 0), st)
    assert _types(ev) == ["kickoff"]
    assert st["started"]


def test_live_status_announces_goals():
    w, st = _w(), _fresh()
    w._process(_live_str(0, 0), st)          # kickoff
    ev = w._process(_live_str(1, 0), st)     # England scores
    assert _types(ev) == ["goal"]
    assert ev[0]["match"]["score"]["fullTime"] == {"home": 1, "away": 0}


def test_live_paused_live_advances_to_second_half():
    # The feed uses LIVE for in-play and PAUSED for the break (England v Ghana 2026-06-23,
    # LIVE→PAUSED at half-time). The PAUSED→LIVE transition must still advance the half so
    # 2nd-half goals are labeled correctly.
    w, st = _w(), _fresh()
    w._process(_live_str(0, 0), st)                          # 1st-half kick-off (LIVE)
    w._process(_live_str(0, 0, status="PAUSED"), st)         # half-time
    assert st["pending_half"] is True
    ev = w._process(_live_str(1, 0), st)                     # 2nd half resumes (LIVE) + a goal
    assert st["half"] == 2
    assert _types(ev) == ["goal"]
    assert ev[0]["half_label"] == "Second Half"


def test_fulltime_from_live_only_feed():
    # A match tracked entirely via LIVE (never IN_PLAY) must still resolve at FINISHED.
    w, st = _w(), _fresh()
    w._process(_live_str(0, 0), st)                          # LIVE kick-off
    w._process(_live_str(1, 0), st)                          # LIVE goal -> 1-0
    fin = {"status": "FINISHED", "homeTeam": {"name": "England"},
           "awayTeam": {"name": "Ghana"}, "score": {"fullTime": {"home": 1, "away": 0}}}
    ev = w._process(fin, st)
    assert _types(ev) == ["fulltime"]
    assert st["finished"]
    assert ev[0]["match"]["score"]["fullTime"] == {"home": 1, "away": 0}


def test_live_mid_match_join_baselines_silently():
    # The phantom-goal guard must hold for the LIVE synonym too: joining at 1-1 (past
    # KICKOFF_GRACE → near_kickoff False) adopts the score silently, no kickoff, no goals.
    st = _fresh()
    events = _w()._process(_live_str(1, 1), st)
    assert events == []
    assert st["started"] and st["score"] == (1, 1)
