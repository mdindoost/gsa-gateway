"""Tests for the MatchWatcher state machine + catch logic (no network)."""
from __future__ import annotations

import asyncio
import datetime
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.integration.match_watcher import MatchWatcher


def mk(status="IN_PLAY", h=0, a=0):
    return {"id": 42, "status": status,
            "homeTeam": {"name": "Mexico"}, "awayTeam": {"name": "South Africa"},
            "score": {"fullTime": {"home": h, "away": a}},
            "stage": "GROUP_STAGE", "group": "GROUP_A", "utcDate": "2026-06-11T19:00:00Z"}


def w():
    return MatchWatcher(["k1", "k2"], ":memory:")


def fresh():
    return {"started": False, "score": (0, 0), "finished": False}


# ── state machine ──────────────────────────────────────────────────────────────
def test_parse_nulls_to_zero():
    assert MatchWatcher._parse(mk("FINISHED", None, None)) == ("FINISHED", (0, 0))
    assert MatchWatcher._parse(mk("IN_PLAY", 2, 1)) == ("IN_PLAY", (2, 1))


def test_first_live_emits_kickoff():
    st = fresh()
    evs = w()._process(mk("IN_PLAY", 0, 0), st)
    assert [e["type"] for e in evs] == ["kickoff"]
    assert st["started"] is True and st["score"] == (0, 0)


def test_late_first_read_near_kickoff_still_emits_kickoff():
    # API reported the live state late: first read is 1-0 but we're still near kickoff.
    # Announce kickoff, adopt the score silently (no back-announced goal).
    st = fresh()
    evs = w()._process(mk("IN_PLAY", 1, 0), st, near_kickoff=True)
    assert [e["type"] for e in evs] == ["kickoff"]
    assert st["started"] is True and st["score"] == (1, 0)


def test_first_read_far_from_kickoff_stays_silent_restart():
    # mid-match restart (not near kickoff): adopt score silently, NO kickoff, NO goals.
    st = fresh()
    evs = w()._process(mk("IN_PLAY", 1, 0), st, near_kickoff=False)
    assert evs == []
    assert st["started"] is True and st["score"] == (1, 0)


def test_score_increase_emits_goal_for_right_team():
    st = {"started": True, "score": (0, 0), "finished": False}
    evs = w()._process(mk("IN_PLAY", 1, 0), st)
    assert [e["type"] for e in evs] == ["goal"]
    assert evs[0]["scoring_team"]["name"] == "Mexico"
    assert evs[0]["match"]["score"]["fullTime"] == {"home": 1, "away": 0}
    assert st["score"] == (1, 0)


def test_two_goal_jump_emits_two_with_distinct_keys():
    st = {"started": True, "score": (1, 0), "finished": False}
    evs = w()._process(mk("IN_PLAY", 2, 1), st)   # +1 home, +1 away in one read
    assert [e["type"] for e in evs] == ["goal", "goal"]
    keys = [MatchWatcher._dedup_key(42, e) for e in evs]
    assert keys == ["42:goal:2-0", "42:goal:2-1"]   # distinct → neither is dropped
    assert st["score"] == (2, 1)


def test_two_home_goals_one_read_walk_scoreline():
    st = {"started": True, "score": (0, 0), "finished": False}
    evs = w()._process(mk("IN_PLAY", 2, 0), st)   # 0-0 -> 2-0 in one read
    keys = [MatchWatcher._dedup_key(42, e) for e in evs]
    assert keys == ["42:goal:1-0", "42:goal:2-0"]  # running scoreline, distinct keys
    assert st["score"] == (2, 0)


def test_score_is_monotonic_stale_read_cannot_lower_it():
    st = {"started": True, "score": (2, 0), "finished": False}
    evs = w()._process(mk("IN_PLAY", 0, 0), st)   # a "live" read that lost the score
    assert evs == []
    assert st["score"] == (2, 0)                   # preserved


def test_finished_uses_stored_score_not_the_empty_read():
    st = {"started": True, "score": (2, 0), "finished": False}
    evs = w()._process(mk("FINISHED", None, None), st)   # FINISHED carries no score
    assert [e["type"] for e in evs] == ["fulltime"]
    assert evs[0]["match"]["score"]["fullTime"] == {"home": 2, "away": 0}
    assert st["finished"] is True


def test_finished_uses_payload_score_when_higher_than_tracked():
    # Real bug (2026-06-18): free API lagged at 1-0 during play, then FINISHED carried the
    # true 4-1. Full-time must announce 4-1, not the stale tracked 1-0.
    st = {"started": True, "score": (1, 0), "finished": False}
    evs = w()._process(mk("FINISHED", 4, 1), st)
    assert [e["type"] for e in evs] == ["fulltime"]
    assert evs[0]["match"]["score"]["fullTime"] == {"home": 4, "away": 1}
    assert st["score"] == (4, 1)


def test_finished_twice_no_duplicate():
    st = {"started": True, "score": (2, 0), "finished": True}
    assert w()._process(mk("FINISHED", None, None), st) == []


def test_dedup_keys():
    goal_ev = {"type": "goal", "match": {"score": {"fullTime": {"home": 2, "away": 0}}}}
    assert MatchWatcher._dedup_key(42, goal_ev) == "42:goal:2-0"
    assert MatchWatcher._dedup_key(42, {"type": "kickoff"}) == "42:kickoff:"
    assert MatchWatcher._dedup_key(42, {"type": "fulltime"}) == "42:fulltime:"


# ── schedule ─────────────────────────────────────────────────────────────────
def test_next_kickoff_picks_soonest_unfinished():
    now = datetime.datetime(2026, 6, 11, 18, 0, 0)
    matches = [
        {"id": 1, "utcDate": "2026-06-11T19:00:00Z", "status": "TIMED"},
        {"id": 2, "utcDate": "2026-06-12T02:00:00Z", "status": "TIMED"},
        {"id": 3, "utcDate": "2026-06-10T19:00:00Z", "status": "FINISHED"},  # past + done
    ]
    r = MatchWatcher._next_kickoff(matches, now)
    assert r[0] == 1


def test_debug_log_writes_when_enabled(tmp_path, monkeypatch):
    import v2.integration.match_watcher as mw
    monkeypatch.setattr(mw, "DEBUG_FILE", tmp_path / "dbg.log")
    monkeypatch.setenv("FOOTBALL_DEBUG_LOG", "true")
    watcher = MatchWatcher(["k1234"], ":memory:")   # reads the flag at construction
    watcher._debug("k1234", mk("IN_PLAY", 1, 0))
    text = (tmp_path / "dbg.log").read_text()
    assert "status=IN_PLAY" in text and "score=1-0" in text and "1234" in text


def test_next_kickoff_none_when_all_done():
    now = datetime.datetime(2026, 7, 20, 0, 0, 0)
    matches = [{"id": 1, "utcDate": "2026-06-11T19:00:00Z", "status": "FINISHED"}]
    assert MatchWatcher._next_kickoff(matches, now) is None


# ── catch (async, mocked fetch + no real sleeps) ─────────────────────────────
def _no_wait(monkeypatch):
    # zero the inter-read waits so _catch runs instantly (real asyncio.sleep(0))
    monkeypatch.setattr("v2.integration.match_watcher.PRIMARY_INTERVAL", 0)
    monkeypatch.setattr("v2.integration.match_watcher.BURST_INTERVAL", 0)


def test_catch_returns_first_live_read(monkeypatch):
    _no_wait(monkeypatch)
    watcher = w(); watcher._running = True
    calls = []
    async def fake_fetch(key, mid, day):
        calls.append(key)
        return mk("IN_PLAY", 1, 0) if len(calls) >= 2 else mk("TIMED")
    monkeypatch.setattr(watcher, "_fetch_match", fake_fetch)
    m = asyncio.run(watcher._catch(42, "2026-06-11"))
    assert m["status"] == "IN_PLAY"
    assert calls == ["k1", "k1"]   # caught on the 2nd primary read; never bursted


def test_catch_none_when_all_stale(monkeypatch):
    _no_wait(monkeypatch)
    watcher = w(); watcher._running = True
    n = []
    async def fake_fetch(key, mid, day):
        n.append(1)
        return mk("TIMED")
    monkeypatch.setattr(watcher, "_fetch_match", fake_fetch)
    m = asyncio.run(watcher._catch(42, "2026-06-11"))
    assert m is None
    assert len(n) == 6 + 12        # 6 primary + 12 burst, all stale
