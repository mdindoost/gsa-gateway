"""Tests for the FIFA World Cup 2026 schedule reference / API auditor."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.integration.wc_schedule import (
    GROUP_STAGE, KNOCKOUT, VENUE_CITY, _TEAM_INDEX,
    city_for, et_date, normalize_team, reconcile, venue_for,
)


# ── data integrity ────────────────────────────────────────────────────────────
def test_counts():
    assert len(GROUP_STAGE) == 72
    assert len(KNOCKOUT) == 32
    assert len(GROUP_STAGE) + len(KNOCKOUT) == 104


def test_group_pairings_are_unique():
    # each group-stage pairing meets once -> the team-set index has no collisions
    assert len(_TEAM_INDEX) == 72


def test_knockout_numbers_contiguous():
    nums = sorted(n for (_d, n, _desc, _v) in KNOCKOUT)
    assert nums == list(range(73, 105))


def test_every_venue_has_a_city():
    venues = {v for (*_x, v) in GROUP_STAGE} | {v for (_d, _n, _desc, v) in KNOCKOUT}
    assert venues <= set(VENUE_CITY), f"venues missing a city label: {venues - set(VENUE_CITY)}"


# ── normalization ─────────────────────────────────────────────────────────────
def test_normalize_aliases():
    assert normalize_team("Korea Republic") == "south korea"
    assert normalize_team("South Korea") == "south korea"
    assert normalize_team("Türkiye") == "turkey"
    assert normalize_team("Cape Verde Islands") == "cape verde"
    assert normalize_team("Cabo Verde") == "cape verde"
    assert normalize_team("Côte d'Ivoire") == "ivory coast"
    assert normalize_team("IR Iran") == "iran"
    assert normalize_team("") == ""


def test_city_for():
    assert city_for("Mexico City Stadium") == "Mexico City"
    assert city_for("BC Place Vancouver") == "Vancouver"
    assert city_for("Unknown Stadium") == "Unknown Stadium"  # falls back


# ── et_date ───────────────────────────────────────────────────────────────────
def test_et_date_rolls_back_utc_evening():
    # 01:00 UTC Jun 12 == 9:00 PM ET Jun 11
    assert et_date("2026-06-12T01:00:00Z") == "2026-06-11"
    # 19:00 UTC Jun 11 == 3:00 PM ET Jun 11
    assert et_date("2026-06-11T19:00:00Z") == "2026-06-11"


def test_et_date_passthrough_and_bad():
    assert et_date("2026-06-11") == "2026-06-11"   # bare date
    assert et_date("") == ""
    assert et_date("garbage") == "garbage"[:10]


# ── venue_for ─────────────────────────────────────────────────────────────────
def test_venue_for_order_independent_and_normalized():
    assert venue_for("Mexico", "South Africa") == "Mexico City"
    assert venue_for("South Africa", "Mexico") == "Mexico City"        # order
    assert venue_for("South Korea", "Czechia") == "Guadalajara"        # API name
    assert venue_for("Korea Republic", "Czechia") == "Guadalajara"     # FIFA name


def test_venue_for_unknown_pairing():
    assert venue_for("Spain", "Brazil") is None  # different groups, never meet


# ── reconcile (the auditor) ───────────────────────────────────────────────────
def test_reconcile_clean_exact():
    city, disc = reconcile("2026-06-11T19:00:00Z", "Mexico", "South Africa")
    assert city == "Mexico City"
    assert disc == []


def test_reconcile_one_day_gap_is_benign():
    # Australia v Türkiye: FIFA 2026-06-13; an ET date of 06-14 is the TZ artifact
    city, disc = reconcile("2026-06-14T05:00:00Z", "Australia", "Turkey")
    assert city == "Vancouver"
    assert disc == []  # ≤1 day -> not flagged


def test_reconcile_real_date_mismatch_flags():
    # same teams, wildly wrong date -> genuine discrepancy, venue still resolved
    city, disc = reconcile("2026-06-20T19:00:00Z", "Mexico", "South Africa")
    assert city == "Mexico City"
    assert disc and "DATE MISMATCH" in disc[0]


def test_reconcile_unknown_team():
    city, disc = reconcile("2026-06-11T19:00:00Z", "Atlantis", "Wakanda")
    assert city is None
    assert disc and "NO FIFA MATCH" in disc[0]
