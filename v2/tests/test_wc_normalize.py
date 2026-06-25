"""Tests for v2.integration.wc_providers.normalize — ESPN scoreboard event → NormMatch.

Pure parser, no network. Driven by a REAL trimmed ESPN scoreboard payload captured
2026-06-24 (v2/tests/fixtures/espn_scoreboard_2026-06-24.json): 6 matches —
2 FULL_TIME (incl. Bosnia 3-1 Qatar with a Goal + Own Goal), 2 HALFTIME, 2 SCHEDULED.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.integration.wc_providers.normalize import scoreboard_to_matches

FIXTURE = Path(__file__).parent / "fixtures" / "espn_scoreboard_2026-06-24.json"


def load():
    return json.loads(FIXTURE.read_text())


def by_id(matches, mid):
    return next(m for m in matches if m.id == mid)


def test_parses_all_six_matches():
    matches = scoreboard_to_matches(load())
    assert len(matches) == 6


def test_match_identity_and_kickoff():
    m = by_id(scoreboard_to_matches(load()), 760462)   # Bosnia v Qatar
    assert m.id == 760462                               # ESPN string id coerced to int
    assert m.utc_date == "2026-06-24T19:00Z"
    assert m.home.name == "Bosnia-Herzegovina"
    assert m.away.name == "Qatar"


def test_status_mapping_full_time_halftime_scheduled():
    matches = scoreboard_to_matches(load())
    assert by_id(matches, 760462).state == "done"        # STATUS_FULL_TIME
    # Morocco v Haiti is STATUS_HALFTIME → paused
    paused = [m for m in matches if m.state == "paused"]
    assert len(paused) == 2
    # the two SCHEDULED matches are uncatchable → state None
    assert len([m for m in matches if m.state is None]) == 2


def test_score_from_competitors():
    m = by_id(scoreboard_to_matches(load()), 760462)
    assert m.score == (3, 1)                              # home 3, away 1


def test_goals_have_scorer_minute_kind_in_order():
    m = by_id(scoreboard_to_matches(load()), 760462)
    goals = m.goals
    assert len(goals) == 4
    # first goal: Kerim Alajbegovic, 29', normal, home team (Bosnia, id 452)
    g0 = goals[0]
    assert g0.scorer == "Kerim Alajbegovic"
    assert g0.minute == "29'"
    assert g0.kind == "goal"
    assert g0.team_id == 452
    assert g0.athlete_id == 391034


def test_own_goal_flagged():
    m = by_id(scoreboard_to_matches(load()), 760462)
    og = next(g for g in m.goals if g.minute == "34'")
    assert og.kind == "own_goal"


def test_goal_identity_is_stable_key():
    m = by_id(scoreboard_to_matches(load()), 760462)
    # identity = (match, athlete, clock) — stable across reads, survives reordering
    keys = [g.identity for g in m.goals]
    assert len(set(keys)) == 4                            # all distinct
    assert m.goals[0].identity == (760462, 391034, "29'")


def test_scheduled_match_has_no_goals_and_no_state():
    matches = scoreboard_to_matches(load())
    sched = [m for m in matches if m.state is None]
    assert all(m.goals == [] for m in sched)


def test_two_anonymous_goals_same_minute_keep_distinct_identities():
    # S1 guard: goals missing athlete_id must NOT collapse to one identity (would drop the
    # 2nd). Build a synthetic event with two scoring plays, both no athlete, same clock.
    from v2.integration.wc_providers.normalize import event_to_match
    ev = {"id": "999", "date": "2026-06-25T01:00Z",
          "competitions": [{"status": {"type": {"state": "in", "name": "STATUS_FIRST_HALF"}},
                            "competitors": [{"homeAway": "home", "score": "2",
                                             "team": {"id": "1", "displayName": "A"}},
                                            {"homeAway": "away", "score": "0",
                                             "team": {"id": "2", "displayName": "B"}}],
                            "details": [
                                {"scoringPlay": True, "type": {"text": "Goal"},
                                 "clock": {"displayValue": "12'"}, "team": {"id": "1"},
                                 "athletesInvolved": []},
                                {"scoringPlay": True, "type": {"text": "Goal"},
                                 "clock": {"displayValue": "12'"}, "team": {"id": "1"},
                                 "athletesInvolved": []}]}]}
    m = event_to_match(ev)
    assert len(m.goals) == 2
    assert m.goals[0].identity != m.goals[1].identity   # distinct despite both anon @12'


def test_own_goal_credited_to_beneficiary_not_scorer_team():
    # Regression for the review's B2: ESPN credits the OG's team_id to the BENEFICIARY.
    # Morocco(home,2869) v Haiti(away,2654): Bounou is MOROCCO's keeper; his 10' OG must
    # credit HAITI (the away side), i.e. score moves 0-1, NOT 1-0.
    m = by_id(scoreboard_to_matches(load()), 760464)
    og = next(g for g in m.goals if g.kind == "own_goal")
    assert og.team_id == 2654                # credited to Haiti (beneficiary)
    assert og.scorer == "Yassine Bounou"     # the Morocco player who put it in
