"""Tests for the v2 World Cup tracker detection logic (no network)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.database.schema import create_all

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


async def _no_previews():
    """Stub for runner tests that exercise only the live-event path."""
    return []


def test_single_key(tmp_path, monkeypatch):
    monkeypatch.setattr(wt, "STATE_FILE", tmp_path / "s.json")
    t = wt.WorldCupTracker("solo")
    assert t.keys == ["solo"]
    assert t._next_headers({"X-Extra": "1"}) == {"X-Auth-Token": "solo", "X-Extra": "1"}


def test_multi_key_round_robin(tmp_path, monkeypatch):
    monkeypatch.setattr(wt, "STATE_FILE", tmp_path / "s.json")
    t = wt.WorldCupTracker("k1, k2 , k3")     # whitespace tolerated
    assert t.keys == ["k1", "k2", "k3"]
    seen = [t._next_headers()["X-Auth-Token"] for _ in range(7)]
    assert seen == ["k1", "k2", "k3", "k1", "k2", "k3", "k1"]


def test_debug_log_writes_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(wt, "STATE_FILE", tmp_path / "s.json")
    monkeypatch.setattr(wt, "DEBUG_FILE", tmp_path / "dbg.log")
    monkeypatch.setenv("FOOTBALL_DEBUG_LOG", "true")
    t = wt.WorldCupTracker("k1,k2")       # reads the flag at construction
    t._next_headers()                      # sets _last_key
    t._debug(104, [mk(status="IN_PLAY", hs=1)])
    text = (tmp_path / "dbg.log").read_text()
    assert "status=IN_PLAY" in text and "score=1-0" in text and "key=" in text


def test_debug_log_off_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(wt, "STATE_FILE", tmp_path / "s.json")
    monkeypatch.setattr(wt, "DEBUG_FILE", tmp_path / "dbg.log")
    monkeypatch.delenv("FOOTBALL_DEBUG_LOG", raising=False)
    t = wt.WorldCupTracker("k")
    t._debug(104, [mk(status="IN_PLAY")])
    assert not (tmp_path / "dbg.log").exists()


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
    tracker.unfold_goals = True  # paid tier: the scorer/minute feed is used
    drive(tracker, [mk(status="IN_PLAY")])
    async def goals(mid):
        return {"goals": [{"minute": 23, "scorer": {"name": "Messi"}, "team": {"name": "Mexico"}}]}
    monkeypatch.setattr(tracker, "get_match", goals)
    ev = drive(tracker, [mk(status="IN_PLAY", hs=1)])
    assert types(ev) == ["goal"]
    assert ev[0]["scorer"] == "Messi" and ev[0]["minute"] == 23


def test_free_tier_skips_get_match(tracker, monkeypatch):
    # default (free tier, unfold_goals=False) must NOT call the paid goal-detail
    # endpoint, yet must still announce the goal via the score-diff path
    assert tracker.unfold_goals is False
    called = []
    async def boom(mid):
        called.append(mid)
        return {}
    monkeypatch.setattr(tracker, "get_match", boom)
    drive(tracker, [mk(status="IN_PLAY")])              # kickoff
    ev = drive(tracker, [mk(status="IN_PLAY", hs=1)])   # goal via score-diff
    assert types(ev) == ["goal"]
    assert called == []  # get_match never hit on the free tier


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


def test_worldcup_runner_enqueues_a_post(monkeypatch, tmp_path):
    import asyncio
    # isolate the tracker's on-disk state file (matches the existing tracker tests)
    monkeypatch.setattr("v2.integration.worldcup_tracker.STATE_FILE", tmp_path / "wc.json")
    from v2.integration.worldcup_runner import WorldCupRunner

    conn = create_all(":memory:")
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(2,'GSA','gsa','gsa')")
    conn.commit()

    runner = WorldCupRunner(registry=None, api_key="k", channel="world-cup-2026",
                            db_path=":memory:", org_slug="gsa")
    runner._conn = conn            # inject the test connection (start() would open its own)
    runner.org_id = 2
    runner.allowed = {"discord", "telegram"}

    async def fake_check():
        return [{"type": "goal", "match": {"id": 42}, "minute": 23,
                 "scoring_team": {"name": "Brazil"}}]
    runner.tracker.check_matches = fake_check
    runner.tracker.check_previews = _no_previews
    # format_event is imported into the runner module's namespace; patch it there
    monkeypatch.setattr("v2.integration.worldcup_runner.format_event",
                        lambda ev: "GOOOOOAL Brazil 1-0")

    asyncio.run(runner._loop_once())

    row = conn.execute("SELECT * FROM posts WHERE type='worldcup'").fetchone()
    assert row is not None
    assert "GOOOOOAL" in row["content"]
    assert row["status"] == "scheduled"
    assert row["discord_channel"] == "world-cup-2026"


def test_runner_posts_consecutive_free_tier_goals(monkeypatch, tmp_path):
    # free-tier goals have NO minute; the 1st (1-0) and 2nd (2-0) goal must BOTH
    # post — they used to collide on "id:goal:" and the 2nd was dropped.
    import asyncio
    monkeypatch.setattr("v2.integration.worldcup_tracker.STATE_FILE", tmp_path / "wc2.json")
    from v2.integration.worldcup_runner import WorldCupRunner
    conn = create_all(":memory:")
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(2,'GSA','gsa','gsa')")
    conn.commit()
    runner = WorldCupRunner(registry=None, api_key="k", channel="world-cup-2026",
                            db_path=":memory:", org_slug="gsa")
    runner._conn = conn; runner.org_id = 2; runner.allowed = {"discord", "telegram"}
    monkeypatch.setattr("v2.integration.worldcup_runner.format_event", lambda ev: "GOAL")

    def goal(h):  # free-tier goal event: no minute, scoreline differs
        return {"type": "goal", "scoring_team": {"name": "Mexico"},
                "match": {"id": 42, "score": {"fullTime": {"home": h, "away": 0}}}}

    async def c1(): return [goal(1)]
    async def c2(): return [goal(2)]
    runner.tracker.check_matches = c1
    runner.tracker.check_previews = _no_previews
    asyncio.run(runner._loop_once())
    runner.tracker.check_matches = c2
    asyncio.run(runner._loop_once())

    n = conn.execute("SELECT COUNT(*) FROM posts WHERE type='worldcup'").fetchone()[0]
    assert n == 2  # both goals posted (was 1 before the scoreline-keyed fix)


# ── kick-off group-standings (single combined post) ──────────────────────────

SAMPLE_ROWS = [
    {"position": 1, "team": {"name": "Spain"}, "playedGames": 2, "won": 1,
     "draw": 1, "lost": 0, "goalDifference": 4, "points": 4},
    {"position": 2, "team": {"name": "Uruguay"}, "playedGames": 2, "won": 0,
     "draw": 1, "lost": 1, "goalDifference": -1, "points": 1},
]


def test_format_standings_lines():
    out = wt.format_standings("GROUP_H", SAMPLE_ROWS)
    assert out.splitlines()[0] == "📊 **Group H**"
    assert "1. Spain — 4 pts · GD +4" in out
    assert "2. Uruguay — 1 pt · GD -1" in out                 # pt singular, signed -GD


def test_format_standings_empty_returns_blank():
    assert wt.format_standings("GROUP_H", []) == ""


def test_format_standings_no_code_fences():
    # GroupMe (plain text) + Telegram (HTML-escapes) can't render ``` monospace
    # blocks — the table MUST be code-fence-free to read on all three channels.
    assert "```" not in wt.format_standings("GROUP_H", SAMPLE_ROWS)


def test_format_standings_defensive_on_missing_fields():
    out = wt.format_standings("GROUP_A", [{"position": 1}])  # bare row
    assert "1. ? — 0 pts · GD 0" in out


def test_fetch_standings_keeps_only_grouped_blocks(tracker, monkeypatch):
    async def fake_get(ep):
        assert ep == "/competitions/WC/standings"
        return {"standings": [
            {"group": "GROUP_A", "table": [{"position": 1}]},
            {"group": None, "table": [{"position": 1}]},      # knockout — dropped
        ]}
    monkeypatch.setattr(tracker, "_get", fake_get)
    out = asyncio.run(tracker.fetch_standings())
    assert list(out) == ["GROUP_A"]


def test_fetch_standings_normalizes_real_api_group_label(tracker, monkeypatch):
    # The standings endpoint labels groups "Group H" but the matches endpoint (and
    # match['group']) uses "GROUP_H". fetch_standings MUST key by the matches format
    # so kickoff/preview lookups with match['group'] resolve. Real-API regression:
    # the kick-off table was silently never appended because the keys didn't match.
    async def fake_get(ep):
        return {"standings": [{"group": "Group H", "table": [{"position": 1}]}]}
    monkeypatch.setattr(tracker, "_get", fake_get)
    out = asyncio.run(tracker.fetch_standings())
    assert list(out) == ["GROUP_H"]                          # not "Group H"


def _kickoff_runner(conn, tmp_path, monkeypatch, state_name):
    monkeypatch.setattr("v2.integration.worldcup_tracker.STATE_FILE", tmp_path / state_name)
    from v2.integration.worldcup_runner import WorldCupRunner
    r = WorldCupRunner(registry=None, api_key="k", channel="world-cup-2026",
                       db_path=":memory:", org_slug="gsa")
    r._conn = conn
    r.org_id = 2
    r.allowed = {"discord", "telegram", "groupme"}
    monkeypatch.setattr("v2.integration.worldcup_runner.format_event",
                        lambda ev: "⚽ **KICK-OFF!**\nSpain vs Uruguay")
    return r


def _org_conn():
    conn = create_all(":memory:")
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(2,'GSA','gsa','gsa')")
    conn.commit()
    return conn


def test_kickoff_post_has_no_table_and_skips_standings(monkeypatch, tmp_path):
    # The group table moved to the pre-match PREVIEW post; the kick-off post is now
    # just the kick-off text and must NOT fetch standings or append a table.
    conn = _org_conn()
    runner = _kickoff_runner(conn, tmp_path, monkeypatch, "ks1.json")

    async def fake_check():
        return [{"type": "kickoff", "match": {"id": 7, "group": "GROUP_H"}, "minute": 0}]
    runner.tracker.check_matches = fake_check
    runner.tracker.check_previews = _no_previews

    called = []
    async def track():
        called.append(1)
        return {}
    runner.tracker.fetch_standings = track

    asyncio.run(runner._loop_once())

    rows = conn.execute("SELECT * FROM posts WHERE type='worldcup'").fetchall()
    assert len(rows) == 1
    assert "KICK-OFF" in rows[0]["content"]                 # kick-off text kept
    assert "📊" not in rows[0]["content"]                   # NO table on kick-off
    assert called == []                                     # standings never fetched on kick-off


def test_goal_event_does_not_fetch_standings(monkeypatch, tmp_path):
    conn = _org_conn()
    runner = _kickoff_runner(conn, tmp_path, monkeypatch, "ks5.json")

    async def fake_check():
        return [{"type": "goal", "match": {"id": 42}, "minute": 23,
                 "scoring_team": {"name": "Brazil"}}]
    runner.tracker.check_matches = fake_check
    runner.tracker.check_previews = _no_previews

    called = []
    async def track():
        called.append(1)
        return {}
    runner.tracker.fetch_standings = track

    asyncio.run(runner._loop_once())

    assert called == []                                    # only kick-offs fetch standings


# ── pre-match preview (Phase B) ──────────────────────────────────────────────
import datetime as _dt

UTC = _dt.timezone.utc
KO = "2026-06-22T01:00:00Z"          # kickoff; default 5-min window opens 2026-06-22T00:55Z


def mkp(mid=900, utc=KO, status="TIMED", group="GROUP_G"):
    return {"id": mid, "status": status, "utcDate": utc, "stage": "GROUP_STAGE",
            "group": group, "homeTeam": {"id": 783, "name": "New Zealand"},
            "awayTeam": {"id": 825, "name": "Egypt"}}


def drive_prev(t, matches, now):
    async def feed():
        return matches
    t.upcoming_for_preview = feed
    return asyncio.run(t.check_previews(now=now))


def test_upcoming_for_preview_uses_two_day_window_and_filters_status(tracker, monkeypatch):
    seen = {}
    async def fake_get(ep, *a, **k):
        seen["ep"] = ep
        return {"matches": [mkp(900, status="TIMED"), mkp(2, status="FINISHED"),
                            mkp(3, status="SCHEDULED")]}
    monkeypatch.setattr(tracker, "_get", fake_get)
    res = asyncio.run(tracker.upcoming_for_preview())
    assert "dateFrom=" in seen["ep"] and "dateTo=" in seen["ep"]
    assert {m["id"] for m in res} == {900, 3}      # TIMED + SCHEDULED, not FINISHED


def test_preview_fires_in_window(tracker):
    now = _dt.datetime(2026, 6, 22, 0, 57, tzinfo=UTC)       # T-3, inside the 5-min window
    assert types(drive_prev(tracker, [mkp()], now)) == ["preview"]


def test_preview_not_before_window(tracker):
    now = _dt.datetime(2026, 6, 22, 0, 50, tzinfo=UTC)       # T-10, before the 5-min window
    assert drive_prev(tracker, [mkp()], now) == []


def test_preview_not_after_kickoff(tracker):
    now = _dt.datetime(2026, 6, 22, 1, 30, tzinfo=UTC)       # T+30, past kickoff
    assert drive_prev(tracker, [mkp()], now) == []


def test_preview_default_lead_is_5_minutes(tracker):
    # No env override -> default lead is 5 min: fires at T-4, not at T-6.
    assert types(drive_prev(tracker, [mkp()], _dt.datetime(2026, 6, 22, 0, 56, tzinfo=UTC))) == ["preview"]  # T-4
    tracker.states.clear()
    assert drive_prev(tracker, [mkp()], _dt.datetime(2026, 6, 22, 0, 54, tzinfo=UTC)) == []                  # T-6


def test_preview_lead_is_configurable(tracker, monkeypatch):
    monkeypatch.setenv("FOOTBALL_PREVIEW_LEAD_MIN", "90")
    now = _dt.datetime(2026, 6, 22, 0, 0, tzinfo=UTC)        # T-60, inside a 90-min window
    assert types(drive_prev(tracker, [mkp()], now)) == ["preview"]


def test_preview_fires_once_only(tracker):
    now = _dt.datetime(2026, 6, 22, 0, 57, tzinfo=UTC)
    assert types(drive_prev(tracker, [mkp()], now)) == ["preview"]
    assert drive_prev(tracker, [mkp()], now) == []           # second tick: already previewed


def test_preview_toggle_off(tracker, monkeypatch):
    monkeypatch.setenv("FOOTBALL_PREVIEW_ENABLED", "false")
    now = _dt.datetime(2026, 6, 22, 0, 57, tzinfo=UTC)
    assert drive_prev(tracker, [mkp()], now) == []


def test_preview_fires_for_next_utc_day_kickoff(tracker):
    # Kickoff 00:02Z is the *next* UTC calendar day vs the window moment (23:59Z the
    # day before) — the trigger must still fire across the UTC-day boundary (B1).
    now = _dt.datetime(2026, 6, 21, 23, 59, tzinfo=UTC)      # T-3, previous UTC day
    assert types(drive_prev(tracker, [mkp(utc="2026-06-22T00:02:00Z")], now)) == ["preview"]


def test_load_state_defaults_preview_announced(tmp_path, monkeypatch):
    import json
    sf = tmp_path / "s.json"
    sf.write_text(json.dumps({"42": {                        # flag-less (pre-feature) state row
        "match_id": 42, "home_team": "A", "away_team": "B", "home_score": 0,
        "away_score": 0, "status": "scheduled", "minute": 0, "goals_announced": [],
        "kickoff_announced": False, "halftime_announced": False,
        "second_half_announced": False, "fulltime_announced": False,
        "stage": "", "group": "", "utc_date": ""}}))
    monkeypatch.setattr(wt, "STATE_FILE", sf)
    t = wt.WorldCupTracker("k")
    assert t.states[42].preview_announced is False           # defaulted, state NOT wiped


def test_runner_enqueues_preview_post(monkeypatch, tmp_path):
    monkeypatch.setattr("v2.integration.worldcup_tracker.STATE_FILE", tmp_path / "wcp.json")
    from v2.integration.worldcup_runner import WorldCupRunner
    conn = create_all(":memory:")
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(2,'GSA','gsa','gsa')")
    conn.commit()
    runner = WorldCupRunner(registry=None, api_key="k", channel="world-cup-2026",
                            db_path=":memory:", org_slug="gsa")
    runner._conn = conn; runner.org_id = 2; runner.allowed = {"discord", "telegram"}

    async def no_matches():
        return []
    async def one_preview():
        return [{"type": "preview", "match": {
            "id": 900, "group": "GROUP_G", "matchday": 2,
            "utcDate": KO, "homeTeam": {"id": 783, "name": "New Zealand"},
            "awayTeam": {"id": 825, "name": "Egypt"}}}]
    async def standings():
        return {"GROUP_G": [{"position": 1, "team": {"name": "New Zealand"},
                             "points": 1, "goalDifference": 0}]}
    runner.tracker.check_matches = no_matches
    runner.tracker.check_previews = one_preview
    runner.tracker.fetch_standings = standings

    asyncio.run(runner._loop_once())
    asyncio.run(runner._loop_once())   # second tick must NOT double-post (dedup)

    rows = conn.execute(
        "SELECT content, json_extract(metadata,'$._dedup_key') AS k, "
        "json_extract(metadata,'$.event_type') AS et FROM posts WHERE type='worldcup'"
    ).fetchall()
    assert len(rows) == 1                                   # exactly one preview, deduped
    assert "MATCH PREVIEW" in rows[0]["content"]
    assert "📊 **Group G**" in rows[0]["content"]           # table present
    assert rows[0]["k"] == "worldcup:900:preview"
    assert rows[0]["et"] == "preview"
