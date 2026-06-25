"""Event-driven ESPN state machine — process_match(NormMatch, ledger) → event dicts.

Goal identity drives dedup/correction (scoreboard-primary design). The emitted event
dicts carry a football-data-shaped ``match`` adapter so the existing ``format_event`` /
``_dedup_key`` keep working, PLUS scorer/minute/kind on goals to light up the dormant
GOAL render branch.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.integration.wc_providers.normalize import NormMatch, TeamRef, GoalEvent
from v2.integration.wc_providers.espn_process import process_match, fresh_ledger
from v2.integration.match_watcher import MatchWatcher


HOME = TeamRef(id=452, name="Bosnia-Herzegovina", abbreviation="BIH")
AWAY = TeamRef(id=4398, name="Qatar", abbreviation="QAT")


def goal(athlete_id, minute, team_id=452, scorer="Scorer", kind="goal"):
    return GoalEvent(match_id=760462, team_id=team_id, athlete_id=athlete_id,
                     scorer=scorer, minute=minute, kind=kind)


def match(state="in_play", score=(0, 0), goals=(), minute=None):
    return NormMatch(id=760462, utc_date="2026-06-24T19:00Z", state=state,
                     home=HOME, away=AWAY, score=score, minute=minute, goals=list(goals))


def test_first_live_read_emits_kickoff():
    led = fresh_ledger()
    evs = process_match(match("in_play", (0, 0)), led)
    assert [e["type"] for e in evs] == ["kickoff"]
    assert led["started"] is True


def test_first_read_with_goals_far_from_kickoff_is_silent_baseline():
    led = fresh_ledger()
    m = match("in_play", (1, 0), [goal(391034, "29'")])
    evs = process_match(m, led, near_kickoff=False)
    assert evs == []                                  # no back-announced goal, no kickoff
    assert led["started"] is True
    assert (760462, 391034, "29'") in led["announced_goals"]


def test_new_goal_emits_with_scorer_minute_and_running_score():
    led = fresh_ledger()
    led["started"] = True
    m = match("in_play", (1, 0), [goal(391034, "29'", scorer="Kerim Alajbegovic")])
    evs = process_match(m, led)
    assert len(evs) == 1
    ev = evs[0]
    assert ev["type"] == "goal"
    assert ev["scorer"] == "Kerim Alajbegovic"
    assert ev["minute"] == "29'"
    assert ev["team"] == "Bosnia-Herzegovina"          # credited side → flag resolves
    assert ev["match"]["score"]["fullTime"] == {"home": 1, "away": 0}


def test_own_goal_kind_and_credited_team():
    led = fresh_ledger()
    led["started"] = True
    # Qatar player scores into own net → credited to home (Bosnia), score 1-0
    m = match("in_play", (1, 0), [goal(999, "34'", team_id=452, scorer="Abunada", kind="own_goal")])
    ev = process_match(m, led)[0]
    assert ev["kind"] == "own_goal"
    assert ev["team"] == "Bosnia-Herzegovina"          # beneficiary credited
    assert ev["scorer"] == "Abunada"


def test_two_new_goals_one_read_emit_in_order_with_running_scorelines():
    led = fresh_ledger()
    led["started"] = True
    g1 = goal(391034, "29'", team_id=452)              # home → 1-0
    g2 = goal(4398001, "42'", team_id=4398)            # away → 1-1
    evs = process_match(match("in_play", (1, 1), [g1, g2]), led)
    assert [e["type"] for e in evs] == ["goal", "goal"]
    assert evs[0]["match"]["score"]["fullTime"] == {"home": 1, "away": 0}
    assert evs[1]["match"]["score"]["fullTime"] == {"home": 1, "away": 1}


def test_already_announced_goal_not_reemitted():
    led = fresh_ledger()
    led["started"] = True
    led["announced_goals"] = {(760462, 391034, "29'")}
    evs = process_match(match("in_play", (1, 0), [goal(391034, "29'")]), led)
    assert evs == []


def test_disallowed_goal_emits_correction_on_healthy_read():
    led = fresh_ledger()
    led["started"] = True
    led["announced_goals"] = {(760462, 391034, "29'"), (760462, 4398001, "42'")}
    # a healthy read (still has one goal) where the 29' goal vanished → VAR disallowed
    m = match("in_play", (0, 1), [goal(4398001, "42'", team_id=4398)])
    evs = process_match(m, led)
    assert [e["type"] for e in evs] == ["correction"]
    assert (760462, 391034, "29'") not in led["announced_goals"]
    assert evs[0]["match"]["score"]["fullTime"] == {"home": 0, "away": 1}


def test_transient_empty_read_does_not_emit_false_correction():
    led = fresh_ledger()
    led["started"] = True
    led["announced_goals"] = {(760462, 391034, "29'")}
    # empty goals (transient/partial read) must NOT wipe the announced goal
    evs = process_match(match("in_play", (0, 0), []), led)
    assert evs == []
    assert (760462, 391034, "29'") in led["announced_goals"]


def test_shootout_state_does_not_walk_goals():
    led = fresh_ledger()
    led["started"] = True
    # in a shootout, NormMatch.goals excludes shootout kicks; ensure no goal/correction spam
    evs = process_match(match("shootout", (1, 1), []), led)
    assert evs == []


def test_goal_event_carries_unique_dedup_key_per_goal_identity():
    # S2: two DIFFERENT goals reaching the SAME scoreline (a disallowed goal re-scored later)
    # must NOT collide on the enqueue dedup_key — posts are immortal, a collision drops the
    # 2nd forever. Score alone is not unique; the goal identity must drive the key.
    led_a = fresh_ledger(); led_a["started"] = True
    ev_a = process_match(match("in_play", (0, 1), [goal(111, "20'", team_id=4398)]), led_a)[0]
    led_b = fresh_ledger(); led_b["started"] = True
    ev_b = process_match(match("in_play", (0, 1), [goal(222, "55'", team_id=4398)]), led_b)[0]
    # both are scoreline 0-1 …
    assert ev_a["match"]["score"]["fullTime"] == ev_b["match"]["score"]["fullTime"]
    # … but the dedup keys must differ (distinct goal identities)
    assert MatchWatcher._dedup_key(760462, ev_a) != MatchWatcher._dedup_key(760462, ev_b)


def test_done_emits_fulltime_once():
    led = fresh_ledger()
    led["started"] = True
    evs = process_match(match("done", (3, 1)), led)
    assert [e["type"] for e in evs] == ["fulltime"]
    assert evs[0]["match"]["score"]["fullTime"] == {"home": 3, "away": 1}
    # second call is silent (already finished)
    assert process_match(match("done", (3, 1)), led) == []
