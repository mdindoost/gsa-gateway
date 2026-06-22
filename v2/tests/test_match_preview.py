"""Tests for the World Cup pre-match preview formatter (pure, no network).

Real data is the NZL–Egypt fixture (match 537366, Group G, matchday 2) verified
against football-data.org on 2026-06-21.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.integration.match_preview import build_match_preview


# ── fixtures (real shapes) ────────────────────────────────────────────────────
MATCH = {
    "id": 537366,
    "utcDate": "2026-06-22T01:00:00Z",
    "status": "TIMED",
    "matchday": 2,
    "stage": "GROUP_STAGE",
    "group": "GROUP_G",
    "homeTeam": {"id": 783, "name": "New Zealand"},
    "awayTeam": {"id": 825, "name": "Egypt"},
    "referees": [{"id": 1, "name": "Omar Mohamed Al Ali", "type": "REFEREE",
                  "nationality": "United Arab Emirates"}],
}

NZ_TEAM = {
    "id": 783, "name": "New Zealand",
    "coach": {"name": "Darren Bazeley", "nationality": "England"},
    "squad": (
        [{"name": f"GK{i}", "position": "Goalkeeper"} for i in range(3)]
        + [{"name": f"D{i}", "position": "Defence"} for i in range(9)]
        + [{"name": f"M{i}", "position": "Midfield"} for i in range(7)]
        + [{"name": f"F{i}", "position": "Offence"} for i in range(7)]
    ),
}

EGY_TEAM = {
    "id": 825, "name": "Egypt",
    "coach": {"name": "Hossam Hassan", "nationality": "Egypt"},
    "squad": (
        [{"name": f"GK{i}", "position": "Goalkeeper"} for i in range(4)]
        + [{"name": f"D{i}", "position": "Defence"} for i in range(8)]
        + [{"name": f"M{i}", "position": "Midfield"} for i in range(9)]
        + [{"name": f"F{i}", "position": "Offence"} for i in range(5)]
    ),
}

STANDINGS = [
    {"position": 1, "team": {"name": "Iran"}, "points": 2, "goalDifference": 0},
    {"position": 2, "team": {"name": "Belgium"}, "points": 2, "goalDifference": 0},
    {"position": 3, "team": {"name": "New Zealand"}, "points": 1, "goalDifference": 0},
    {"position": 4, "team": {"name": "Egypt"}, "points": 1, "goalDifference": 0},
]

KICKOFF = "8:00 PM ET"
VENUE = "Kansas City"


def full(**over):
    kw = dict(match=MATCH, home_team=NZ_TEAM, away_team=EGY_TEAM, h2h={},
              standings_rows=STANDINGS, venue=VENUE, kickoff_et=KICKOFF)
    kw.update(over)
    return build_match_preview(**kw)


# ── full render ───────────────────────────────────────────────────────────────
def test_full_preview_contains_all_blocks():
    out = full()
    assert "⏳ MATCH PREVIEW" in out
    assert "New Zealand vs" in out and "Egypt" in out
    assert "8:00 PM ET" in out and "Group G" in out and "Matchday 2" in out
    assert "📍 Kansas City" in out
    assert "Omar Mohamed Al Ali" in out
    assert "Iran" in out                              # standings block reused
    assert "Coach: Darren Bazeley" in out and "Coach: Hossam Hassan" in out


def test_squad_counts_render_and_sum_to_total():
    out = full()
    assert "26 players · GK 3 · DEF 9 · MID 7 · FWD 7" in out
    assert "26 players · GK 4 · DEF 8 · MID 9 · FWD 5" in out


# ── head-to-head: honest-partial ──────────────────────────────────────────────
def test_h2h_empty_renders_no_meetings():
    out = full(h2h={})
    assert "No previous World Cup meetings" in out


def test_h2h_empty_when_zero_matches():
    out = full(h2h={"aggregates": {"numberOfMatches": 0}})
    assert "No previous World Cup meetings" in out


def test_h2h_inconsistent_aggregate_falls_back():
    # numberOfMatches says 1 but W/D/L all zero — the API counted the just-finished
    # fixture in the count but hasn't folded it into the results yet. The numbers
    # don't sum, so DON'T render a misleading "Played 1 · 0–0 · 0 draws" line;
    # fall back to the honest no-meetings line (observed live: NZL–Egypt, 2026-06-22).
    h2h = {"aggregates": {
        "numberOfMatches": 1,
        "homeTeam": {"id": 783, "wins": 0, "draws": 0, "losses": 0},
        "awayTeam": {"id": 825, "wins": 0, "draws": 0, "losses": 0},
    }}
    out = full(h2h=h2h)
    assert "No previous World Cup meetings" in out
    assert "Played 1" not in out


def test_h2h_present_aligned_orientation():
    # aggregates.homeTeam.id == match home id (783 = NZ) -> NZ wins 1, Egypt wins 2
    h2h = {"aggregates": {
        "numberOfMatches": 4,
        "homeTeam": {"id": 783, "wins": 1, "draws": 1, "losses": 2},
        "awayTeam": {"id": 825, "wins": 2, "draws": 1, "losses": 1},
    }}
    out = full(h2h=h2h)
    assert "Played 4" in out
    assert "New Zealand 1–2 Egypt" in out
    assert "1 draw" in out
    assert "No previous World Cup meetings" not in out


def test_h2h_present_reversed_orientation_is_corrected():
    # API oriented to the OTHER fixture: aggregates.homeTeam is Egypt (825).
    # NZ is the match home, so the line must still read "New Zealand {nz_w}–{egy_w} Egypt".
    h2h = {"aggregates": {
        "numberOfMatches": 4,
        "homeTeam": {"id": 825, "wins": 2, "draws": 1, "losses": 1},  # Egypt
        "awayTeam": {"id": 783, "wins": 1, "draws": 1, "losses": 2},  # New Zealand
    }}
    out = full(h2h=h2h)
    assert "New Zealand 1–2 Egypt" in out


def test_h2h_neither_id_matches_falls_back_to_no_meetings():
    h2h = {"aggregates": {
        "numberOfMatches": 4,
        "homeTeam": {"id": 999, "wins": 1, "draws": 1, "losses": 2},
        "awayTeam": {"id": 888, "wins": 2, "draws": 1, "losses": 1},
    }}
    out = full(h2h=h2h)
    assert "No previous World Cup meetings" in out


def test_h2h_single_draw_singular_wording():
    h2h = {"aggregates": {
        "numberOfMatches": 1,
        "homeTeam": {"id": 783, "wins": 0, "draws": 1, "losses": 0},
        "awayTeam": {"id": 825, "wins": 0, "draws": 1, "losses": 0},
    }}
    out = full(h2h=h2h)
    assert "1 draw" in out and "1 draws" not in out


# ── omit-never-fake ───────────────────────────────────────────────────────────
def test_missing_coach_omits_coach_clause():
    nz = {**NZ_TEAM, "coach": {}}
    out = full(home_team=nz)
    assert "Coach:" in out                 # Egypt still has one
    # NZ line present (squad still shown) but no coach clause for NZ
    assert "New Zealand — Coach:" not in out
    assert "GK 3" in out


def test_missing_venue_omits_pin_line():
    out = full(venue=None)
    assert "📍" not in out


def test_missing_referee_omits_ref_line():
    m = {**MATCH, "referees": []}
    out = full(match=m)
    assert "👤" not in out


def test_missing_matchday_omits_matchday():
    m = {**MATCH}
    m.pop("matchday")
    out = full(match=m)
    assert "Matchday" not in out


def test_missing_team_data_omits_that_squad_block():
    out = full(home_team=None)
    assert "Coach: Hossam Hassan" in out     # Egypt block remains
    assert "Coach: Darren Bazeley" not in out
    assert "GK 3" not in out                  # NZ squad counts not shown


# ── squad bucket edge cases ───────────────────────────────────────────────────
def test_null_position_buckets_to_other():
    nz = {**NZ_TEAM, "squad": NZ_TEAM["squad"] + [{"name": "X", "position": None}]}
    out = full(home_team=nz)
    assert "27 players" in out and "Other 1" in out


def test_other_bucket_hidden_when_empty():
    out = full()
    assert "Other" not in out


# ── channel safety (GroupMe / Telegram) ───────────────────────────────────────
def test_no_code_fences_or_markdown_tables():
    out = full()
    assert "```" not in out
    assert "|" not in out          # no markdown tables
