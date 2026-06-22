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


# ── half labelling (derive the half from the PAUSED transitions, no minute) ──────
def test_goal_in_first_half_labeled_first_half():
    st = {"started": True, "score": (0, 0), "finished": False}
    evs = w()._process(mk("IN_PLAY", 1, 0), st)
    assert evs[0]["half_label"] == "First Half"


def test_goal_after_pause_labeled_second_half():
    st = {"started": True, "score": (1, 0), "finished": False}
    wt = w()
    wt._process(mk("PAUSED", 1, 0), st)            # half-time break, no goal
    evs = wt._process(mk("IN_PLAY", 2, 0), st)     # second-half goal
    assert [e["type"] for e in evs] == ["goal"]
    assert evs[0]["half_label"] == "Second Half"


def test_goals_revealed_at_pause_labeled_first_half():
    # API hid the goals during play; the PAUSED read reveals 3-0 → all first-half goals
    # (the score was scored before the break, so the half-time read belongs to the 1st half).
    st = {"started": True, "score": (0, 0), "finished": False}
    evs = w()._process(mk("PAUSED", 3, 0), st)
    assert [e["half_label"] for e in evs] == ["First Half", "First Half", "First Half"]


def test_third_period_after_second_pause_labeled_extra_time():
    # Knockout: PAUSED happens again at 90' before extra time. Anything past the 2nd resume
    # is beyond regulation → "Extra Time" until we observe a real ET match's API behaviour.
    st = {"started": True, "score": (1, 1), "finished": False}
    wt = w()
    wt._process(mk("PAUSED", 1, 1), st)            # half-time
    wt._process(mk("IN_PLAY", 1, 1), st)           # second half
    wt._process(mk("PAUSED", 1, 1), st)            # end of 90', before extra time
    evs = wt._process(mk("IN_PLAY", 2, 1), st)     # extra-time goal
    assert evs[0]["half_label"] == "Extra Time"


def test_format_event_goal_includes_half_label():
    from v2.integration.worldcup_tracker import format_event
    ev = {"type": "goal", "scoring_team": {"name": "Mexico"},
          "match": mk("IN_PLAY", 2, 0), "half_label": "Second Half"}
    out = format_event(ev)
    assert "Second Half" in out


# ── state persistence (the JSON ledger survives a restart) ───────────────────────
def _w_with_state(tmp_path):
    return MatchWatcher(["k1"], ":memory:", state_file=tmp_path / "mw_state.json")


def test_save_then_load_roundtrips_ledger(tmp_path):
    w1 = _w_with_state(tmp_path)
    w1._states[537336] = {"started": True, "score": (4, 0), "finished": False,
                          "half": 2, "pending_half": False,
                          "score_updated": "2026-06-18T20:00:00Z", "correction_gen": 0,
                          "preview_posted": True}
    w1.save_states()
    w2 = _w_with_state(tmp_path)
    w2.load_states()
    assert w2._states[537336] == {"started": True, "score": (4, 0), "finished": False,
                                  "half": 2, "pending_half": False,
                                  "score_updated": "2026-06-18T20:00:00Z", "correction_gen": 0,
                                  "preview_posted": True}


def test_match_state_returns_fresh_for_unknown_match(tmp_path):
    w = _w_with_state(tmp_path)
    st = w._match_state(99)
    assert st == {"started": False, "score": (0, 0), "finished": False,
                  "half": 1, "pending_half": False,
                  "score_updated": None, "correction_gen": 0,
                  "preview_posted": False}
    assert w._states[99] is st        # registered so a later save persists it


def test_match_state_resumes_persisted_ledger(tmp_path):
    w = _w_with_state(tmp_path)
    w._states[42] = {"started": True, "score": (3, 0), "finished": False,
                     "half": 2, "pending_half": False}
    assert w._match_state(42)["score"] == (3, 0) and w._match_state(42)["half"] == 2


def test_resumed_ledger_reconciles_missed_goal_with_correct_half(tmp_path):
    # The whole point: after a restart we announced up to 4-0 in the 2nd half; the API now
    # reports 5-0. We must announce 5-0 (NOT silent, NOT first half) from the restored ledger.
    w = _w_with_state(tmp_path)
    st = {"started": True, "score": (4, 0), "finished": False,
          "half": 2, "pending_half": False}
    evs = w._process(mk("IN_PLAY", 5, 0), st)
    assert [e["type"] for e in evs] == ["goal"]
    assert evs[0]["half_label"] == "Second Half"
    assert st["score"] == (5, 0)


def test_process_then_save_persists_updated_ledger(tmp_path):
    w = _w_with_state(tmp_path)
    st = w._match_state(42)
    st["started"] = True
    w._process(mk("IN_PLAY", 1, 0), st)   # 0-0 -> 1-0 goal
    w.save_states()
    w2 = _w_with_state(tmp_path); w2.load_states()
    assert w2._states[42]["score"] == (1, 0)


def test_load_prunes_finished_entries(tmp_path):
    # A wrapped-up match must not linger in the ledger across a restart — it would feed the
    # "instant return" path and (with API lag) spin the scheduler.
    path = tmp_path / "mw_state.json"
    path.write_text(
        '{"1": {"started": true, "score": [2,0], "finished": true,  "half": 2, "pending_half": false},'
        ' "2": {"started": true, "score": [1,0], "finished": false, "half": 1, "pending_half": false}}')
    w = _w_with_state(tmp_path); w.load_states()
    assert 1 not in w._states and 2 in w._states


def test_next_kickoff_skips_ledger_finished_match():
    # The API can still report a just-finished match as IN_PLAY (stale cache). If our ledger
    # says it's done, the scheduler must NOT re-pick it (else _watch instant-returns and spins).
    now = datetime.datetime(2026, 6, 18, 23, 0, 0)
    matches = [
        {"id": 537336, "utcDate": "2026-06-18T22:05:00Z", "status": "IN_PLAY"},  # stale-live
        {"id": 99,     "utcDate": "2026-06-18T23:30:00Z", "status": "TIMED"},
    ]
    assert MatchWatcher._next_kickoff(matches, now)[0] == 537336              # soonest, no ledger
    assert MatchWatcher._next_kickoff(matches, now, finished_ids={537336})[0] == 99  # skipped


def test_load_normalizes_missing_half_keys(tmp_path):
    # A ledger written before half-tracking existed must default cleanly, not KeyError.
    path = tmp_path / "mw_state.json"
    path.write_text('{"42": {"started": true, "score": [2, 0], "finished": false}}')
    w = _w_with_state(tmp_path); w.load_states()
    st = w._states[42]
    assert st["score"] == (2, 0) and st["half"] == 1 and st["pending_half"] is False


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


# ── score correction (VAR / disallowed goal) ────────────────────────────────────
def mk_lu(status, h, a, lu):
    m = mk(status, h, a)
    m["lastUpdated"] = lu
    return m


def test_var_disallowed_goal_corrects_down_and_posts_correction():
    # Belgium v Iran 2026-06-21: goal 0-1 stood ~28 min, then VAR disallowed it; the API
    # corrected to 0-0 (fresh lastUpdated) and the match ended 0-0.
    wt = w(); st = wt._match_state(537365); st["started"] = True
    g = wt._process(mk_lu("IN_PLAY", 0, 1, "2026-06-21T19:31:13Z"), st)
    assert [e["type"] for e in g] == ["goal"] and st["score"] == (0, 1)
    c = wt._process(mk_lu("PAUSED", 0, 0, "2026-06-21T19:59:25Z"), st)
    assert [e["type"] for e in c] == ["correction"]
    assert c[0]["match"]["score"]["fullTime"] == {"home": 0, "away": 0}
    assert st["score"] == (0, 0)
    f = wt._process(mk_lu("FINISHED", 0, 0, "2026-06-21T21:06:37Z"), st)
    assert [e["type"] for e in f] == ["fulltime"]
    assert f[0]["match"]["score"]["fullTime"] == {"home": 0, "away": 0}


def test_fresh_empty_payload_does_not_correct():
    # A fresh-timestamped but EMPTY (None-None) read must not lower a real score.
    wt = w(); st = wt._match_state(42); st["started"] = True
    wt._process(mk_lu("IN_PLAY", 1, 0, "2026-06-21T19:31:13Z"), st)
    evs = wt._process(mk_lu("IN_PLAY", None, None, "2026-06-21T19:59:25Z"), st)
    assert evs == [] and st["score"] == (1, 0)


def test_equal_lastupdated_lower_score_no_correction():
    # The correcting read must be STRICTLY newer; an equal timestamp is the same snapshot.
    wt = w(); st = wt._match_state(42); st["started"] = True
    wt._process(mk_lu("IN_PLAY", 1, 0, "2026-06-21T19:31:13Z"), st)
    evs = wt._process(mk_lu("PAUSED", 0, 0, "2026-06-21T19:31:13Z"), st)
    assert evs == [] and st["score"] == (1, 0)


def test_decrease_with_no_prior_stamp_does_not_correct():
    # No score_updated yet (e.g. a silently-adopted baseline) → keep monotonic protection.
    wt = w(); st = {"started": True, "score": (1, 0), "finished": False}
    evs = wt._process(mk_lu("PAUSED", 0, 0, "2026-06-21T19:59:25Z"), st)
    assert evs == [] and st["score"] == (1, 0)


def test_finished_lower_with_fresh_lastupdated_is_trusted():
    # Catch-late: we miss the live PAUSED reads and only see FINISHED 0-0 (fresh) — full-time
    # must be 0-0, not the stale tracked 0-1.
    wt = w(); st = wt._match_state(537365); st["started"] = True
    wt._process(mk_lu("IN_PLAY", 0, 1, "2026-06-21T19:31:13Z"), st)
    f = wt._process(mk_lu("FINISHED", 0, 0, "2026-06-21T21:06:37Z"), st)
    assert [e["type"] for e in f] == ["fulltime"]
    assert f[0]["match"]["score"]["fullTime"] == {"home": 0, "away": 0}
    assert st["score"] == (0, 0)


def test_rescore_after_correction_not_deduped():
    # 0-1 disallowed → 0-0 → Iran scores 0-1 for real. The second 0-1 goal must NOT collide
    # with the disallowed one's dedup key.
    wt = w(); st = wt._match_state(42); st["started"] = True
    g1 = wt._process(mk_lu("IN_PLAY", 0, 1, "2026-06-21T19:31:13Z"), st)
    wt._process(mk_lu("PAUSED", 0, 0, "2026-06-21T19:59:25Z"), st)
    g2 = wt._process(mk_lu("IN_PLAY", 0, 1, "2026-06-21T20:40:00Z"), st)
    assert [e["type"] for e in g2] == ["goal"]
    k1 = MatchWatcher._dedup_key(42, g1[0])
    k2 = MatchWatcher._dedup_key(42, g2[0])
    assert k1 != k2


def test_correction_dedup_key_includes_gen():
    ev = {"type": "correction", "gen": 1, "match": {"score": {"fullTime": {"home": 0, "away": 0}}}}
    assert MatchWatcher._dedup_key(42, ev) == "42:correction:1:0-0"


def test_goals_from_both_sides_resume_correctly_after_correction():
    # After a correction back to 0-0, a goal from EITHER side must continue the running
    # scoreline with a distinct (non-colliding) dedup key.
    wt = w(); st = wt._match_state(42); st["started"] = True
    wt._process(mk_lu("IN_PLAY", 0, 1, "2026-06-21T19:31:13Z"), st)   # Iran 0-1
    c = wt._process(mk_lu("PAUSED", 0, 0, "2026-06-21T19:59:25Z"), st)   # disallowed → 0-0
    assert [e["type"] for e in c] == ["correction"] and st["score"] == (0, 0)
    g_home = wt._process(mk_lu("IN_PLAY", 1, 0, "2026-06-21T20:20:00Z"), st)   # Belgium 1-0
    assert [e["type"] for e in g_home] == ["goal"]
    assert g_home[0]["scoring_team"]["name"] == "Mexico"   # home team in mk()
    assert g_home[0]["match"]["score"]["fullTime"] == {"home": 1, "away": 0}
    g_away = wt._process(mk_lu("IN_PLAY", 1, 1, "2026-06-21T20:40:00Z"), st)   # Iran 1-1
    assert [e["type"] for e in g_away] == ["goal"]
    assert g_away[0]["match"]["score"]["fullTime"] == {"home": 1, "away": 1}
    assert st["score"] == (1, 1)
    # every posted key after the correction is distinct from the disallowed goal's
    keys = [MatchWatcher._dedup_key(42, e) for e in (g_home + g_away)]
    assert keys == ["42:goal:1:1-0", "42:goal:1:1-1"]


def test_second_correction_same_line_not_deduped():
    # 0-0 → goal 1-0 → that goal also disallowed → 0-0 again. The 2nd correction must post,
    # not collide with the 1st correction's dedup key.
    wt = w(); st = wt._match_state(42); st["started"] = True
    wt._process(mk_lu("IN_PLAY", 0, 1, "2026-06-21T19:31:13Z"), st)   # 0-1
    c1 = wt._process(mk_lu("PAUSED", 0, 0, "2026-06-21T19:59:25Z"), st)   # → 0-0  (corr gen 1)
    wt._process(mk_lu("IN_PLAY", 1, 0, "2026-06-21T20:20:00Z"), st)   # 1-0
    c2 = wt._process(mk_lu("PAUSED", 0, 0, "2026-06-21T20:40:00Z"), st)   # → 0-0  (corr gen 2)
    assert [e["type"] for e in c2] == ["correction"] and st["score"] == (0, 0)
    assert MatchWatcher._dedup_key(42, c1[0]) != MatchWatcher._dedup_key(42, c2[0])


def test_format_event_correction():
    from v2.integration.worldcup_tracker import format_event
    out = format_event({"type": "correction", "match": mk("PAUSED", 0, 0)})
    assert "correction" in out.lower()


def test_correction_persists_across_save_load(tmp_path):
    w1 = _w_with_state(tmp_path); st = w1._match_state(42); st["started"] = True
    w1._process(mk_lu("IN_PLAY", 0, 1, "2026-06-21T19:31:13Z"), st)
    w1._process(mk_lu("PAUSED", 0, 0, "2026-06-21T19:59:25Z"), st)
    w1.save_states()
    w2 = _w_with_state(tmp_path); w2.load_states()
    assert w2._states[42]["score"] == (0, 0)
    assert w2._states[42]["score_updated"] == "2026-06-21T19:59:25Z"


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


# ── pre-match preview (matchup + group table at T-5) ─────────────────────────
from v2.core.database.schema import create_all   # noqa: E402


def _conn_org():
    conn = create_all(":memory:")
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(2,'GSA','gsa','gsa')")
    conn.commit()
    return conn


def _preview_match():
    return {"id": 7, "group": "GROUP_H", "matchday": 2,
            "utcDate": "2026-06-11T19:00:00Z",
            "homeTeam": {"id": 1, "name": "Spain"}, "awayTeam": {"id": 2, "name": "Uruguay"}}


PREVIEW_ROWS = [
    {"position": 1, "team": {"name": "Spain"}, "points": 4, "goalDifference": 4},
    {"position": 2, "team": {"name": "Uruguay"}, "points": 1, "goalDifference": -1},
]


def test_fresh_ledger_and_normalize_have_preview_posted():
    assert MatchWatcher._fresh_ledger()["preview_posted"] is False
    # a pre-feature record (no preview_posted) normalizes to False, never KeyErrors
    assert MatchWatcher._normalize({"started": True})["preview_posted"] is False


def test_fetch_standings_normalizes_group_key(monkeypatch):
    mw = w()
    async def fake_get(key, url):
        assert url.endswith("/competitions/WC/standings")
        return {"standings": [{"group": "Group H", "table": [{"position": 1}]},
                              {"group": None, "table": [{"position": 1}]}]}   # knockout dropped
    monkeypatch.setattr(mw, "_get", fake_get)
    out = asyncio.run(mw._fetch_standings("k"))
    assert list(out) == ["GROUP_H"]                 # "Group H" -> matches-format "GROUP_H"


def test_post_preview_enqueues_table_once(monkeypatch):
    mw = w(); mw._conn = _conn_org(); mw.org_id = 2
    async def fake_match(key, mid, day):
        return _preview_match()
    async def fake_standings(key):
        return {"GROUP_H": PREVIEW_ROWS}
    monkeypatch.setattr(mw, "_fetch_match", fake_match)
    monkeypatch.setattr(mw, "_fetch_standings", fake_standings)

    assert asyncio.run(mw._post_preview(7, "2026-06-11")) is True
    assert asyncio.run(mw._post_preview(7, "2026-06-11")) is True   # dedup at posts layer

    rows = mw._conn.execute(
        "SELECT content, json_extract(metadata,'$._dedup_key') AS k, "
        "json_extract(metadata,'$.event_type') AS et FROM posts WHERE type='worldcup'"
    ).fetchall()
    assert len(rows) == 1                            # one preview, deduped
    assert "MATCH PREVIEW" in rows[0]["content"]
    assert "📊 **Group H**" in rows[0]["content"]    # the group table
    assert "Spain vs" in rows[0]["content"]
    assert rows[0]["k"] == "worldcup:7:preview"
    assert rows[0]["et"] == "preview"


def test_post_preview_toggle_off(monkeypatch):
    monkeypatch.setenv("FOOTBALL_PREVIEW_ENABLED", "false")
    mw = w(); mw._conn = _conn_org(); mw.org_id = 2
    async def boom(*a, **k):
        raise AssertionError("must not fetch when disabled")
    monkeypatch.setattr(mw, "_fetch_match", boom)
    assert asyncio.run(mw._post_preview(7, "2026-06-11")) is False
    assert mw._conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0


def test_post_preview_skipped_when_match_unavailable(monkeypatch):
    mw = w(); mw._conn = _conn_org(); mw.org_id = 2
    async def no_match(key, mid, day):
        return None
    monkeypatch.setattr(mw, "_fetch_match", no_match)
    assert asyncio.run(mw._post_preview(7, "2026-06-11")) is False
    assert mw._conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0


# ── readability: blank line after the header on the live event posts ─────────
def test_event_posts_have_blank_line_after_header():
    from v2.integration.worldcup_tracker import format_event
    ko = format_event({"type": "kickoff", "match": mk("IN_PLAY")})
    assert "**KICK-OFF!**\n\n" in ko
    goal = format_event({"type": "goal", "scoring_team": {"name": "Egypt"},
                         "match": mk("IN_PLAY", 0, 1)})
    assert "**GOAL!** 🇪🇬 Egypt\n\n" in goal
    ft = format_event({"type": "fulltime", "match": mk("FINISHED", 2, 1)})
    assert "**FULL-TIME**\n\n" in ft
