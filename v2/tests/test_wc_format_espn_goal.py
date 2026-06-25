"""format_event rendering of ESPN-style goal events (scorer + minute + OG/pen kind).

These exercise the previously-dormant scorer/minute branch in worldcup_tracker.format_event,
now fed by the ESPN state machine. Football-data goals (no scorer) are unaffected.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.integration.worldcup_tracker import format_event


def goal_ev(scorer="Kerim Alajbegovic", minute="29'", kind="goal",
            team="Bosnia-Herzegovina", score=(1, 0)):
    return {"type": "goal", "scorer": scorer, "minute": minute, "kind": kind,
            "team": team, "half_label": "First Half",
            "scoring_team": {"name": team},
            "match": {"homeTeam": {"name": "Bosnia-Herzegovina"},
                      "awayTeam": {"name": "Qatar"},
                      "score": {"fullTime": {"home": score[0], "away": score[1]}}}}


def test_goal_renders_scorer_and_single_quoted_minute():
    out = format_event(goal_ev())
    assert "GOAL!" in out
    assert "Kerim Alajbegovic 29'" in out          # exactly one apostrophe, not 29''
    assert "29''" not in out
    assert "🇧🇦" in out                             # flag resolves from team name


def test_goal_renders_scoreline_and_half():
    out = format_event(goal_ev(score=(1, 0)))
    assert "1–0" in out
    assert "First Half" in out


def test_own_goal_tagged_OG():
    out = format_event(goal_ev(scorer="Abunada", minute="34'", kind="own_goal", score=(2, 0)))
    assert "Abunada" in out
    assert "(OG)" in out


def test_penalty_tagged_pen():
    out = format_event(goal_ev(scorer="Vargas", minute="46'", kind="penalty", score=(1, 0)))
    assert "Vargas" in out
    assert "(pen)" in out


def test_stoppage_time_minute_kept_verbatim():
    out = format_event(goal_ev(minute="45'+2'"))
    assert "45'+2'" in out
    assert "45'+2''" not in out


def test_football_data_team_only_goal_still_works():
    # the old shape (no scorer) must still render via the team-only branch
    ev = {"type": "goal", "scoring_team": {"name": "Brazil"}, "half_label": "Second Half",
          "match": {"homeTeam": {"name": "Scotland"}, "awayTeam": {"name": "Brazil"},
                    "score": {"fullTime": {"home": 0, "away": 2}}}}
    out = format_event(ev)
    assert "GOAL!" in out and "Brazil" in out and "0–2" in out
