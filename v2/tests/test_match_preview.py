"""Tests for the World Cup pre-match preview formatter (pure, no network).

The preview is intentionally minimal: matchup + kickoff/group context + the live
group table. (Squads, coaches, head-to-head, venue and referee were removed — the
free API's H2H is unreliable and the rest added noise; the group table is the value.)
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.integration.match_preview import build_match_preview


MATCH = {
    "id": 537399, "utcDate": "2026-06-22T17:00:00Z", "status": "TIMED",
    "matchday": 2, "stage": "GROUP_STAGE", "group": "GROUP_J",
    "homeTeam": {"id": 100, "name": "Argentina"},
    "awayTeam": {"id": 200, "name": "Austria"},
}
STANDINGS = [
    {"position": 1, "team": {"name": "Argentina"}, "points": 3, "goalDifference": 3},
    {"position": 2, "team": {"name": "Austria"}, "points": 3, "goalDifference": 2},
    {"position": 3, "team": {"name": "Jordan"}, "points": 0, "goalDifference": -2},
    {"position": 4, "team": {"name": "Algeria"}, "points": 0, "goalDifference": -3},
]
KICKOFF = "1:00 PM ET"


def full(**over):
    kw = dict(match=MATCH, standings_rows=STANDINGS, kickoff_et=KICKOFF)
    kw.update(over)
    return build_match_preview(**kw)


def test_preview_has_header_matchup_context_and_table():
    out = full()
    assert "⏳ **MATCH PREVIEW**" in out
    assert "Argentina vs" in out and "Austria" in out
    assert "1:00 PM ET" in out and "Group J" in out and "Matchday 2" in out
    assert "📊 **Group J**" in out
    assert "1. Argentina — 3 pts" in out


def test_preview_drops_removed_blocks():
    out = full()
    for gone in ("Coach", "players", "Head-to-head", "📍", "👤", "Ref:"):
        assert gone not in out


def test_no_matchday_omits_matchday():
    m = {**MATCH}; m.pop("matchday")
    out = full(match=m)
    assert "Matchday" not in out
    assert "Group J" in out


def test_knockout_uses_stage_and_no_table_when_no_rows():
    m = {**MATCH}; m.pop("group"); m["stage"] = "ROUND_OF_16"
    out = full(match=m, standings_rows=[])
    assert "Round Of 16" in out
    assert "📊" not in out


def test_empty_standings_omits_table():
    out = full(standings_rows=[])
    assert "📊" not in out
    assert "Argentina vs" in out          # header still present


def test_no_code_fences_or_markdown_tables():
    out = full()
    assert "```" not in out
    assert "|" not in out
