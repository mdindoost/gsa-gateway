"""Tests for FIFA World Cup 2026 live notification system + Telegram broadcasting."""

import asyncio
import json
import tempfile
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.services.worldcup_embeds import format_score, kickoff_to_et
from bot.services.worldcup_tracker import FLAG_MAP, MatchState, WorldCupTracker


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_match(
    match_id=1,
    home="Brazil",
    away="Argentina",
    status="IN_PLAY",
    home_score=0,
    away_score=0,
    stage="GROUP_STAGE",
    group="GROUP_A",
    goals=None,
):
    return {
        "id": match_id,
        "utcDate": "2026-06-11T19:00:00Z",
        "status": status,
        "minute": 45 if status == "IN_PLAY" else None,
        "homeTeam": {"id": 10, "name": home, "shortName": home, "crest": ""},
        "awayTeam": {"id": 11, "name": away, "shortName": away, "crest": ""},
        "score": {
            "fullTime": {"home": home_score, "away": away_score},
            "halfTime": {"home": 0, "away": 0},
        },
        "goals": goals or [],
        "stage": stage,
        "group": group,
    }


def _make_client(todays=None, full_match=None):
    client = MagicMock()
    client.get_todays_matches = AsyncMock(return_value=todays or [])
    client.get_match = AsyncMock(return_value=full_match or {})
    return client


def _make_tracker(client, state_file=None):
    with patch.object(WorldCupTracker, "load_state"):
        tracker = WorldCupTracker(client)
    if state_file:
        tracker._state_file = state_file
    return tracker


# ── format_team_name ──────────────────────────────────────────────────────────

def test_format_team_name_with_flag():
    client = _make_client()
    tracker = _make_tracker(client)
    result = tracker.format_team_name({"name": "Brazil"})
    assert result == "🇧🇷 Brazil"


def test_format_team_name_unknown():
    client = _make_client()
    tracker = _make_tracker(client)
    result = tracker.format_team_name({"name": "Unknown Team"})
    assert result == "⚽ Unknown Team"


# ── format_score ──────────────────────────────────────────────────────────────

def test_format_score_normal():
    match = _make_match(home_score=2, away_score=1)
    assert format_score(match) == "2 — 1"


def test_format_score_none_values():
    match = _make_match()
    match["score"]["fullTime"] = {"home": None, "away": None}
    assert format_score(match) == "0 — 0"


# ── kickoff_to_et ─────────────────────────────────────────────────────────────

def test_kickoff_to_et():
    # 2026-06-11T19:00:00Z = 3:00 PM EDT
    result = kickoff_to_et("2026-06-11T19:00:00Z")
    assert result == "3:00 PM ET"


# ── goal detection ────────────────────────────────────────────────────────────

def test_goal_detection():
    goal = {
        "minute": 23,
        "type": "NORMAL",
        "team": {"id": 10, "name": "Brazil"},
        "scorer": {"name": "Vinicius Jr."},
        "assist": None,
    }
    full_match = _make_match(home_score=1, away_score=0, goals=[goal])
    client = _make_client(
        todays=[_make_match(home_score=1, away_score=0, goals=[goal])],
        full_match=full_match,
    )
    tracker = _make_tracker(client)
    # Set a previous state with 0-0 score and status live so kickoff isn't re-fired
    tracker.states[1] = MatchState(
        match_id=1, home_team="Brazil", away_team="Argentina",
        home_score=0, away_score=0, status="live", minute=20,
        goals_announced=[], kickoff_announced=True,
        halftime_announced=False, second_half_announced=False, fulltime_announced=False,
        stage="GROUP_STAGE", group="GROUP_A",
        utc_date="2026-06-11T19:00:00Z",
    )

    events = asyncio.get_event_loop().run_until_complete(tracker.check_matches())
    goal_events = [e for e in events if e["type"] == "goal"]
    assert len(goal_events) == 1
    assert goal_events[0]["scorer"] == "Vinicius Jr."
    assert goal_events[0]["minute"] == 23


# ── duplicate goal prevention ─────────────────────────────────────────────────

def test_duplicate_goal_prevention():
    goal = {
        "minute": 23,
        "type": "NORMAL",
        "team": {"id": 10, "name": "Brazil"},
        "scorer": {"name": "Vinicius Jr."},
        "assist": None,
    }
    already_announced = [
        {"minute": 23, "scorer": "Vinicius Jr.", "team": "Brazil"}
    ]
    full_match = _make_match(home_score=1, away_score=0, goals=[goal])
    client = _make_client(
        todays=[_make_match(home_score=1, away_score=0, goals=[goal])],
        full_match=full_match,
    )
    tracker = _make_tracker(client)
    tracker.states[1] = MatchState(
        match_id=1, home_team="Brazil", away_team="Argentina",
        home_score=1, away_score=0, status="live", minute=25,
        goals_announced=already_announced, kickoff_announced=True,
        halftime_announced=False, second_half_announced=False, fulltime_announced=False,
        stage="GROUP_STAGE", group="GROUP_A",
        utc_date="2026-06-11T19:00:00Z",
    )

    events = asyncio.get_event_loop().run_until_complete(tracker.check_matches())
    goal_events = [e for e in events if e["type"] == "goal"]
    assert len(goal_events) == 0


# ── state persistence ─────────────────────────────────────────────────────────

def test_state_persists_to_file():
    import bot.services.worldcup_tracker as wt_module
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "worldcup_state.json"
        with patch.object(wt_module, "STATE_FILE", state_file):
            client = _make_client(todays=[_make_match(status="IN_PLAY")])
            tracker = _make_tracker(client)
            # Pre-seed so kickoff isn't re-fired
            tracker.states[1] = MatchState(
                match_id=1, home_team="Brazil", away_team="Argentina",
                home_score=0, away_score=0, status="live", minute=10,
                goals_announced=[], kickoff_announced=True,
                halftime_announced=False, second_half_announced=False, fulltime_announced=False,
                stage="GROUP_STAGE", group="GROUP_A",
                utc_date="2026-06-11T19:00:00Z",
            )
            asyncio.get_event_loop().run_until_complete(tracker.check_matches())

        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert "1" in data
        assert data["1"]["match_id"] == 1


# ── state loads on startup ────────────────────────────────────────────────────

def test_state_loads_on_startup():
    import bot.services.worldcup_tracker as wt_module
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "worldcup_state.json"
        saved = {
            "42": {
                "match_id": 42,
                "home_team": "France",
                "away_team": "Germany",
                "home_score": 1,
                "away_score": 0,
                "status": "live",
                "minute": 60,
                "goals_announced": [],
                "kickoff_announced": True,
                "halftime_announced": False,
                "fulltime_announced": False,
                "stage": "GROUP_STAGE",
                "group": "GROUP_B",
                "utc_date": "2026-06-12T18:00:00Z",
            }
        }
        state_file.write_text(json.dumps(saved))

        with patch.object(wt_module, "STATE_FILE", state_file):
            client = _make_client()
            tracker = _make_tracker(client)  # load_state is patched out in __init__
            tracker.states = {}
            tracker.load_state()  # call it directly with STATE_FILE patched

        assert 42 in tracker.states
        assert tracker.states[42].home_team == "France"
        assert tracker.states[42].kickoff_announced is True


# ── no crash on API error ─────────────────────────────────────────────────────

def test_no_crash_on_api_error():
    client = _make_client(todays=[])
    # Simulate API returning empty dict (error case)
    client.get_todays_matches = AsyncMock(return_value=[])
    tracker = _make_tracker(client)

    events = asyncio.get_event_loop().run_until_complete(tracker.check_matches())
    assert events == []


def test_no_crash_when_get_match_returns_empty():
    """Score change detected but get_match returns {} — should fire synthetic goal event."""
    client = _make_client(
        todays=[_make_match(home_score=1, away_score=0)],
        full_match={},  # API error for full match (free tier: empty goals)
    )
    tracker = _make_tracker(client)
    tracker.states[1] = MatchState(
        match_id=1, home_team="Brazil", away_team="Argentina",
        home_score=0, away_score=0, status="live", minute=10,
        goals_announced=[], kickoff_announced=True,
        halftime_announced=False, second_half_announced=False, fulltime_announced=False,
        stage="GROUP_STAGE", group="GROUP_A",
        utc_date="2026-06-11T19:00:00Z",
    )

    events = asyncio.get_event_loop().run_until_complete(tracker.check_matches())
    # Free-tier path: no crash AND a synthetic goal event is fired from the score diff
    goal_events = [e for e in events if e["type"] == "goal"]
    assert len(goal_events) == 1
    assert goal_events[0]["scoring_team"]["name"] == "Brazil"


# ── Telegram broadcasting ─────────────────────────────────────────────────────

# NOTE: test_telegram_broadcast_goal was removed in the 2026-06-10 v1→v2 cut —
# it exercised bot/services/scheduler.py::_broadcast_wc_event, which was deleted
# along with SchedulerCog (v2 now owns all autonomous outbound). The World Cup
# tracker/client tests above remain valid.


def test_telegram_broadcast_skipped_no_config():
    """broadcast() returns False when no target is configured."""
    from bot.services.telegram_broadcaster import TelegramBroadcaster

    with patch("bot.services.telegram_broadcaster.config") as mock_cfg:
        mock_cfg.telegram_broadcast_target = ""
        broadcaster = TelegramBroadcaster.__new__(TelegramBroadcaster)
        broadcaster._bot = MagicMock()

        result = asyncio.get_event_loop().run_until_complete(
            broadcaster.broadcast("hello")
        )

    assert result is False
    broadcaster._bot.send_message.assert_not_called()


def test_telegram_broadcast_photo_fallback():
    """broadcast_photo falls back to text when image file is missing."""
    from bot.services.telegram_broadcaster import TelegramBroadcaster

    with patch("bot.services.telegram_broadcaster.config") as mock_cfg:
        mock_cfg.telegram_broadcast_target = "-1001234567890"
        broadcaster = TelegramBroadcaster.__new__(TelegramBroadcaster)
        broadcaster._bot = MagicMock()
        broadcaster._bot.send_message = AsyncMock(return_value=True)

        result = asyncio.get_event_loop().run_until_complete(
            broadcaster.broadcast_photo(
                photo_path="/nonexistent/image.png",
                caption="Caption text",
            )
        )

    # Should fall back to send_message (not send_photo)
    broadcaster._bot.send_message.assert_called_once()
    assert result is True
