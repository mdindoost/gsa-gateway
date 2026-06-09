"""Tests for the v2 World Cup tracker detection logic (no network)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import v2.integration.worldcup_tracker as wt


def mk(mid=1, status="TIMED", hs=0, as_=0, home="Mexico", away="South Africa"):
    return {"id": mid, "status": status, "minute": 0,
            "homeTeam": {"name": home}, "awayTeam": {"name": away},
            "score": {"fullTime": {"home": hs, "away": as_}},
            "stage": "GROUP_STAGE", "group": "GROUP_A", "utcDate": "2026-06-11T19:00:00Z"}


@pytest.fixture()
def tracker(tmp_path, monkeypatch):
    monkeypatch.setattr(wt, "STATE_FILE", tmp_path / "state.json")
    t = wt.WorldCupTracker("dummy-key")
    async def no_goals(mid):  # free-tier path by default
        return {}
    monkeypatch.setattr(t, "get_match", no_goals)
    return t


def drive(t, matches):
    async def feed():
        return matches
    t.get_todays_matches = feed
    return asyncio.run(t.check_matches())


def types(events):
    return [e["type"] for e in events]


def test_kickoff(tracker):
    assert types(drive(tracker, [mk(status="IN_PLAY")])) == ["kickoff"]


def test_goal_free_tier(tracker):
    drive(tracker, [mk(status="IN_PLAY")])                 # kickoff
    assert types(drive(tracker, [mk(status="IN_PLAY", hs=1)])) == ["goal"]


def test_full_lifecycle_and_dedup(tracker):
    assert types(drive(tracker, [mk(status="IN_PLAY")])) == ["kickoff"]
    assert types(drive(tracker, [mk(status="IN_PLAY", hs=1)])) == ["goal"]
    assert drive(tracker, [mk(status="IN_PLAY", hs=1)]) == []          # no duplicate goal
    assert types(drive(tracker, [mk(status="PAUSED", hs=1)])) == ["halftime"]
    assert types(drive(tracker, [mk(status="IN_PLAY", hs=1)])) == ["second_half"]
    assert types(drive(tracker, [mk(status="FINISHED", hs=1)])) == ["fulltime"]
    assert drive(tracker, [mk(status="FINISHED", hs=1)]) == []         # no duplicate fulltime


def test_goal_premium_tier(tracker, monkeypatch):
    drive(tracker, [mk(status="IN_PLAY")])
    async def goals(mid):
        return {"goals": [{"minute": 23, "scorer": {"name": "Messi"}, "team": {"name": "Mexico"}}]}
    monkeypatch.setattr(tracker, "get_match", goals)
    ev = drive(tracker, [mk(status="IN_PLAY", hs=1)])
    assert types(ev) == ["goal"]
    assert ev[0]["scorer"] == "Messi" and ev[0]["minute"] == 23


def test_state_survives_reload(tracker, tmp_path, monkeypatch):
    drive(tracker, [mk(status="IN_PLAY", hs=1)])           # kickoff + goal, saved to state file
    t2 = wt.WorldCupTracker("dummy-key")                   # fresh tracker loads the same state
    async def no_goals(mid):
        return {}
    monkeypatch.setattr(t2, "get_match", no_goals)
    assert drive(t2, [mk(status="IN_PLAY", hs=1)]) == []   # already-announced → nothing new


def test_format_kickoff_and_goal():
    ko = wt.format_event({"type": "kickoff", "match": mk(status="IN_PLAY")})
    assert "KICK-OFF" in ko and "Mexico" in ko and "🇲🇽" in ko
    goal = wt.format_event({"type": "goal", "match": mk(hs=1), "scorer": "Messi", "team": "Mexico", "minute": 23})
    assert "GOAL" in goal and "Messi" in goal and "23'" in goal
