"""Shadow comparator — join ESPN NormMatch vs football-data match dicts (pure, no network).

Posts nothing; used by the standalone shadow script to report per-match agreement and,
over time, which source reflects a goal first. Join is by KICKOFF (schedule data, reliable
in both sources) since team-name spellings differ between providers.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.integration.wc_providers.normalize import NormMatch, TeamRef
from v2.integration.wc_providers.shadow import kickoff_key, compare


def nm(mid, ud, state, score):
    return NormMatch(id=mid, utc_date=ud, state=state,
                     home=TeamRef(1, "Bosnia-Herzegovina"), away=TeamRef(2, "Qatar"),
                     score=score)


def fd(ud, status, h, a):
    return {"id": 9, "utcDate": ud, "status": status,
            "homeTeam": {"name": "Bosnia-H."}, "awayTeam": {"name": "Qatar"},
            "score": {"fullTime": {"home": h, "away": a}}}


def test_kickoff_key_normalizes_both_formats():
    # ESPN "Z" minute precision vs football-data ":00Z" second precision → same key
    assert kickoff_key("2026-06-24T19:00Z") == kickoff_key("2026-06-24T19:00:00Z")


def test_join_matches_by_kickoff():
    espn = [nm(760462, "2026-06-24T19:00Z", "in_play", (1, 0))]
    fdm = [fd("2026-06-24T19:00:00Z", "IN_PLAY", 1, 0)]
    rows = compare(espn, fdm)
    assert len(rows) == 1
    assert rows[0]["matched"] is True


def test_scores_agree_flag():
    rows = compare([nm(1, "2026-06-24T19:00Z", "in_play", (1, 0))],
                   [fd("2026-06-24T19:00:00Z", "IN_PLAY", 1, 0)])
    assert rows[0]["scores_agree"] is True


def test_score_disagreement_detected():
    # ESPN shows the goal, football-data lagging at 0-0 → disagree (ESPN ahead)
    rows = compare([nm(1, "2026-06-24T19:00Z", "in_play", (1, 0))],
                   [fd("2026-06-24T19:00:00Z", "IN_PLAY", 0, 0)])
    assert rows[0]["scores_agree"] is False
    assert rows[0]["espn_score"] == (1, 0)
    assert rows[0]["fd_score"] == (0, 0)


def test_state_agreement_maps_football_data_status():
    # football-data PAUSED == ESPN paused; FINISHED == done
    rows = compare([nm(1, "2026-06-24T19:00Z", "paused", (1, 0))],
                   [fd("2026-06-24T19:00:00Z", "PAUSED", 1, 0)])
    assert rows[0]["states_agree"] is True


def test_unmatched_espn_match_reported():
    rows = compare([nm(1, "2026-06-24T22:00Z", "in_play", (0, 0))], [])
    assert rows[0]["matched"] is False
    assert rows[0]["source"] == "espn-only"


def test_paired_simultaneous_kickoffs_join_to_correct_counterparts():
    # FIFA runs two matches at the SAME kickoff — must NOT cross-join on kickoff alone.
    def nm2(mid, ud, h, hn, an, score):
        return NormMatch(id=mid, utc_date=ud, state="done",
                         home=TeamRef(h, hn), away=TeamRef(h + 1, an), score=score)
    espn = [nm2(1, "2026-06-24T19:00Z", 10, "Bosnia-Herzegovina", "Qatar", (3, 1)),
            nm2(2, "2026-06-24T19:00Z", 20, "Switzerland", "Canada", (2, 1))]
    # football-data, SAME kickoff, listed in the OPPOSITE order + variant spellings
    fdm = [{"id": 8, "utcDate": "2026-06-24T19:00:00Z", "status": "FINISHED",
            "homeTeam": {"name": "Switzerland"}, "awayTeam": {"name": "Canada"},
            "score": {"fullTime": {"home": 2, "away": 1}}},
           {"id": 9, "utcDate": "2026-06-24T19:00:00Z", "status": "FINISHED",
            "homeTeam": {"name": "Bosnia-H."}, "awayTeam": {"name": "Qatar"},
            "score": {"fullTime": {"home": 3, "away": 1}}}]
    rows = compare(espn, fdm)
    by_team = {r["teams"]: r for r in rows}
    # each ESPN match joined to its TRUE counterpart → scores agree
    assert by_team["Bosnia-Herzegovina v Qatar"]["scores_agree"] is True
    assert by_team["Switzerland v Canada"]["scores_agree"] is True
