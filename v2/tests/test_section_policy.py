"""Section-routing policy for the multi-college KG expansion (2026-06-18).

A college /our-people page is a faculty roll-up that also carries the dean's office; HCAD packs
two schools onto one listing. route() decides which org each person is appointed to (or skip)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.section_policy import route


# ── no policy → legacy passthrough ────────────────────────────────────────────────
def test_no_policy_returns_default():
    assert route(None, "Professors", "faculty", "nce") == "nce"


# ── college_admin_only: keep admin/staff, skip rolled-up faculty ───────────────────
def test_college_admin_only_keeps_dean_office():
    assert route("college_admin_only", "Office of the Dean Administration", "admin", "nce") == "nce"

def test_college_admin_only_keeps_department_chairs():
    assert route("college_admin_only", "Department Chairs", "admin", "nce") == "nce"

def test_college_admin_only_keeps_misc_college_staff():
    # "Makerspace" maps to category None → college-level staff, kept on the college
    assert route("college_admin_only", "Makerspace", None, "nce") == "nce"

def test_college_admin_only_skips_faculty_rollup():
    for sec in ("Professors", "Associate Professors", "University Lecturers",
                "Distinguished Professors"):
        assert route("college_admin_only", sec, "faculty", "nce") is None

def test_college_admin_only_skips_emeritus_and_joint():
    assert route("college_admin_only", "Professor Emeritus", "emeritus", "nce") is None
    assert route("college_admin_only", "Joint Appointments", "joint", "nce") is None


# ── hcad_split: route the two schools, keep leadership/staff on the college ─────────
def test_hcad_split_architecture_to_njsoa():
    assert route("hcad_split", "Architecture Faculty", "faculty", "hcad") == "njsoa"
    assert route("hcad_split", "University Lecturers, Architecture", "faculty", "hcad") == "njsoa"

def test_hcad_split_artdesign_to_art_design():
    assert route("hcad_split", "Art + Design Faculty", "faculty", "hcad") == "art-design"
    assert route("hcad_split", "University Lecturers, Art + Design", "faculty", "hcad") == "art-design"

def test_hcad_split_leadership_and_staff_stay_on_college():
    assert route("hcad_split", "Leadership", "admin", "hcad") == "hcad"
    assert route("hcad_split", "Staff", "staff", "hcad") == "hcad"
    assert route("hcad_split", "Professors of Practice", "faculty", "hcad") == "hcad"

def test_hcad_split_skips_university_library():
    # the university library cross-lists staff on the HCAD page — not an HCAD appointment
    assert route("hcad_split", "Library Staff", "staff", "hcad") is None


def test_unknown_policy_raises():
    import pytest
    with pytest.raises(ValueError):
        route("nope", "Professors", "faculty", "nce")
