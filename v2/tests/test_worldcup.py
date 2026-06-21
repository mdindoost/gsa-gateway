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


def test_kickoff_post_includes_group_standings(monkeypatch, tmp_path):
    conn = _org_conn()
    runner = _kickoff_runner(conn, tmp_path, monkeypatch, "ks1.json")

    async def fake_check():
        return [{"type": "kickoff", "match": {"id": 7, "group": "GROUP_H"}, "minute": 0}]
    runner.tracker.check_matches = fake_check

    async def fake_standings():
        return {"GROUP_H": SAMPLE_ROWS}
    runner.tracker.fetch_standings = fake_standings

    asyncio.run(runner._loop_once())

    rows = conn.execute("SELECT * FROM posts WHERE type='worldcup'").fetchall()
    assert len(rows) == 1                                   # ONE combined post
    content = rows[0]["content"]
    assert "KICK-OFF" in content                            # kick-off text kept
    assert "📊 **Group H**" in content                      # table appended
    assert "1. Spain — 4 pts" in content


def test_kickoff_knockout_has_no_table(monkeypatch, tmp_path):
    conn = _org_conn()
    runner = _kickoff_runner(conn, tmp_path, monkeypatch, "ks2.json")

    async def fake_check():
        return [{"type": "kickoff", "match": {"id": 7}, "minute": 0}]   # no group
    runner.tracker.check_matches = fake_check

    called = []
    async def track():
        called.append(1)
        return {}
    runner.tracker.fetch_standings = track

    asyncio.run(runner._loop_once())

    rows = conn.execute("SELECT * FROM posts WHERE type='worldcup'").fetchall()
    assert len(rows) == 1
    assert "📊" not in rows[0]["content"]
    assert called == []                                    # standings never fetched


def test_kickoff_standings_failure_degrades_to_plain(monkeypatch, tmp_path):
    conn = _org_conn()
    runner = _kickoff_runner(conn, tmp_path, monkeypatch, "ks3.json")

    async def fake_check():
        return [{"type": "kickoff", "match": {"id": 7, "group": "GROUP_H"}, "minute": 0}]
    runner.tracker.check_matches = fake_check

    async def boom():
        raise RuntimeError("api down")
    runner.tracker.fetch_standings = boom

    asyncio.run(runner._loop_once())                       # must NOT raise

    rows = conn.execute("SELECT * FROM posts WHERE type='worldcup'").fetchall()
    assert len(rows) == 1                                   # kick-off still posts
    assert "KICK-OFF" in rows[0]["content"]
    assert "📊" not in rows[0]["content"]


def test_kickoff_standings_toggle_off(monkeypatch, tmp_path):
    monkeypatch.setenv("FOOTBALL_KICKOFF_STANDINGS", "false")   # read in __init__
    conn = _org_conn()
    runner = _kickoff_runner(conn, tmp_path, monkeypatch, "ks4.json")

    async def fake_check():
        return [{"type": "kickoff", "match": {"id": 7, "group": "GROUP_H"}, "minute": 0}]
    runner.tracker.check_matches = fake_check

    called = []
    async def track():
        called.append(1)
        return {}
    runner.tracker.fetch_standings = track

    asyncio.run(runner._loop_once())

    rows = conn.execute("SELECT * FROM posts WHERE type='worldcup'").fetchall()
    assert len(rows) == 1
    assert "📊" not in rows[0]["content"]
    assert called == []


def test_goal_event_does_not_fetch_standings(monkeypatch, tmp_path):
    conn = _org_conn()
    runner = _kickoff_runner(conn, tmp_path, monkeypatch, "ks5.json")

    async def fake_check():
        return [{"type": "goal", "match": {"id": 42}, "minute": 23,
                 "scoring_team": {"name": "Brazil"}}]
    runner.tracker.check_matches = fake_check

    called = []
    async def track():
        called.append(1)
        return {}
    runner.tracker.fetch_standings = track

    asyncio.run(runner._loop_once())

    assert called == []                                    # only kick-offs fetch standings
