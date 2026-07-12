"""Knockout-stage coverage: extra time + penalty shootouts.

The live ESPN watcher used to go silent on any match past 90' (MATCH_MAX=2h30m retired it
mid-overtime) and never captured the shootout result. These tests pin the fix end to end:
  * normalize.py  — capture finish_kind / shootout_score / winner_side (real ESPN fixtures)
  * espn_process  — the DONE branch attaches AET / penalty markers WITHOUT changing dedup
  * format_event  — render "(AET)" / "win N–M on penalties", backward-compatible

Real captured payloads: 760513 SUI@ARG 3-1 AET, 760508 COL@SUI 0-0 (SUI win 4-3 pens).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.integration.wc_providers.normalize import (
    scoreboard_to_matches, NormMatch, TeamRef)
from v2.integration.wc_providers.espn_process import process_match, fresh_ledger
from v2.integration.worldcup_tracker import format_event
from v2.integration.match_watcher import MatchWatcher

FIX = Path(__file__).parent / "fixtures"


def _one(fixture):
    return scoreboard_to_matches(json.loads((FIX / fixture).read_text()))[0]


# ── normalize: real ESPN fixtures ────────────────────────────────────────────
def test_penalty_final_captures_shootout_and_winner():
    m = _one("espn_scoreboard_pen_760508.json")        # SUI 0-0 COL, SUI win 4-3 pens
    assert m.state == "done"
    assert m.finish_kind == "penalties"
    assert m.score == (0, 0)                            # regulation score, NOT the pens
    assert m.shootout_score == (4, 3)                   # home SUI 4, away COL 3
    assert m.winner_side == "home"                      # derived from the higher pen score


def test_aet_final_has_no_shootout():
    m = _one("espn_scoreboard_aet_760513.json")         # ARG 3-1 SUI (AET)
    assert m.state == "done"
    assert m.finish_kind == "aet"
    assert m.score == (3, 1)
    assert m.shootout_score is None
    assert m.winner_side is None


def test_regulation_final_is_regulation_kind():
    # the pre-existing 2026-06-24 fixture: Bosnia 3-1 Qatar, plain STATUS_FULL_TIME
    matches = scoreboard_to_matches(
        json.loads((FIX / "espn_scoreboard_2026-06-24.json").read_text()))
    m = next(x for x in matches if x.id == 760462)
    assert m.state == "done"
    assert m.finish_kind == "regulation"
    assert m.shootout_score is None and m.winner_side is None


def test_missing_shootout_score_never_coerced_to_zero():
    # a penalty status with NO shootoutScore field must not fabricate a (0,0) result
    ev = {"id": "1", "date": "2026-07-07T00:00Z", "competitions": [{
        "status": {"type": {"name": "STATUS_FINAL_PEN", "state": "post", "completed": True}},
        "competitors": [{"homeAway": "home", "score": "1", "team": {"id": "1", "displayName": "H"}},
                        {"homeAway": "away", "score": "1", "team": {"id": "2", "displayName": "A"}}]}]}
    m = scoreboard_to_matches({"events": [ev]})[0]
    assert m.finish_kind == "penalties"
    assert m.shootout_score is None                     # not (0, 0)
    assert m.winner_side is None


# ── espn_process: DONE branch markers + dedup invariant ──────────────────────
def _nm(state="done", score=(0, 0), finish_kind=None, shootout_score=None, winner_side=None):
    return NormMatch(id=760462, utc_date="2026-07-07T00:00Z", state=state,
                     home=TeamRef(1, "Switzerland"), away=TeamRef(2, "Colombia"),
                     score=score, finish_kind=finish_kind,
                     shootout_score=shootout_score, winner_side=winner_side)


def test_done_penalties_attaches_shootout_keys():
    led = fresh_ledger(); led["started"] = True
    ev = process_match(_nm(score=(0, 0), finish_kind="penalties",
                           shootout_score=(4, 3), winner_side="home"), led)[0]
    assert ev["type"] == "fulltime"
    assert ev["shootout_score"] == (4, 3)
    assert ev["winner_side"] == "home"
    assert "aet" not in ev
    assert "uid" not in ev                              # F5: must not change the dedup key


def test_done_aet_attaches_aet_marker_only():
    led = fresh_ledger(); led["started"] = True
    ev = process_match(_nm(score=(3, 1), finish_kind="aet"), led)[0]
    assert ev["aet"] is True
    assert "shootout_score" not in ev
    assert "uid" not in ev


def test_fulltime_dedup_key_unchanged_by_markers():
    # a penalty/AET fulltime must dedup identically to a plain fulltime → one final post
    led1 = fresh_ledger(); led1["started"] = True
    plain = process_match(_nm(score=(2, 1), finish_kind="regulation"), led1)[0]
    led2 = fresh_ledger(); led2["started"] = True
    pens = process_match(_nm(score=(0, 0), finish_kind="penalties",
                             shootout_score=(4, 3), winner_side="home"), led2)[0]
    assert MatchWatcher._dedup_key(760462, plain) == "760462:fulltime:"
    assert MatchWatcher._dedup_key(760462, pens) == "760462:fulltime:"


# ── format_event rendering ───────────────────────────────────────────────────
def _match_dict(hs=0, as_=0):
    return {"homeTeam": {"name": "Switzerland"}, "awayTeam": {"name": "Colombia"},
            "score": {"fullTime": {"home": hs, "away": as_}}, "stage": "", "group": ""}


def test_format_penalty_fulltime_names_winner_and_pens():
    ev = {"type": "fulltime", "match": _match_dict(0, 0),
          "shootout_score": (4, 3), "winner_side": "home"}
    out = format_event(ev)
    assert "FULL-TIME" in out
    assert "Switzerland win 4–3 on penalties" in out


def test_format_penalty_winner_away_orders_pens_winner_first():
    ev = {"type": "fulltime", "match": _match_dict(0, 0),
          "shootout_score": (3, 5), "winner_side": "away"}
    out = format_event(ev)
    assert "Colombia win 5–3 on penalties" in out


def test_format_penalty_ambiguous_winner_states_no_winner():
    ev = {"type": "fulltime", "match": _match_dict(1, 1),
          "shootout_score": (4, 4), "winner_side": None}
    out = format_event(ev)
    assert "Decided on penalties (4–4)" in out
    assert "win" not in out


def test_format_aet_fulltime_marked():
    ev = {"type": "fulltime", "match": _match_dict(3, 1), "aet": True}
    out = format_event(ev)
    assert "After extra time" in out


def test_format_regulation_fulltime_plain_and_backward_compatible():
    # a football-data / regulation event carries none of the new keys → unchanged output:
    # exactly the header + score line, no trailing marker.
    ev = {"type": "fulltime", "match": _match_dict(2, 1)}
    out = format_event(ev)
    assert out.startswith("🏁 **FULL-TIME**\n\n")
    assert out.count("\n\n") == 1 and "\n_" not in out    # no italic marker line appended
    assert "penalties" not in out and "extra time" not in out.lower()


# ── extra-time goal path (label) ─────────────────────────────────────────────
def test_extra_time_goal_labelled_extra_time():
    from v2.integration.wc_providers.normalize import GoalEvent
    led = fresh_ledger(); led["started"] = True
    m = NormMatch(id=760462, utc_date="2026-07-11T00:00Z", state="in_play",
                  home=TeamRef(1, "Argentina"), away=TeamRef(2, "Switzerland"),
                  score=(2, 1), goals=[GoalEvent(760462, 1, 99, "Scorer", "105'", "goal")])
    ev = process_match(m, led)[0]
    assert ev["type"] == "goal"
    assert ev["half_label"] == "Extra Time"
