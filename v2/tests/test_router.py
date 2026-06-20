"""Deterministic router tests — Phase 1 (structured-query routing).

Fixture uses org ids 4=YWCC, 5=Computer Science to match the plan.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.database.schema import create_all
from v2.core.retrieval.router import route


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    # Insert a minimal org hierarchy:
    # id=4 → YWCC (college), id=5 → Computer Science (child of YWCC)
    c.execute(
        "INSERT INTO organizations(id, name, slug, type) "
        "VALUES (4, 'YWCC', 'ywcc', 'college')"
    )
    c.execute(
        "INSERT INTO organizations(id, parent_id, name, slug, type) "
        "VALUES (5, 4, 'Computer Science', 'computer-science', 'department')"
    )
    c.commit()
    yield c
    c.close()


# ── pre-existing Phase-1 routing tests ────────────────────────────────────────

def test_routes_area_query_to_people_by_research_area(conn):
    r = route(conn, "who works on graph in CS?")
    assert r is not None and r.skill == "people_by_research_area"
    assert r.args["area"] == "graph"
    assert r.args["org_id"] == 5


def test_routes_count_query_to_count_people_by_research_area(conn):
    r = route(conn, "how many people work on graph in CS?")
    assert r is not None and r.skill == "count_people_by_research_area"
    assert r.args["area"] == "graph"
    assert r.args["org_id"] == 5


def test_routes_department_query_to_org_departments(conn):
    r = route(conn, "what departments are in YWCC?")
    assert r is not None and r.skill == "org_departments"
    assert r.args["org_id"] == 4


def test_naming_a_department_does_not_route_to_org_departments(conn):
    # NAMING the org ("how about in CS department", "the CS department") must not be read as
    # "enumerate its sub-departments" (no enum verb → cue doesn't fire).
    for q in ("how about in computer science department?", "the computer science department"):
        r = route(conn, q)
        assert r is None or r.skill != "org_departments", f"{q!r} -> {r}"


def test_singular_enumeration_still_routes(conn):
    # SHOULD #1: singular enumeration with a verb still reaches org_departments (YWCC has children).
    r = route(conn, "what department does YWCC have?")
    assert r is not None and r.skill == "org_departments" and r.args["org_id"] == 4


def test_leaf_department_enumeration_does_not_deflect(conn):
    # SHOULD #2 (defense in depth): even WITH an enumeration cue, a LEAF dept (CS has no
    # type='department' children) must NOT route to org_departments → no false 'no info' deflect.
    for q in ("what departments are in computer science?", "list the departments of computer science"):
        r = route(conn, q)
        assert r is None or r.skill != "org_departments", f"{q!r} -> {r}"


def test_routes_faculty_query_to_faculty_in_department(conn):
    r = route(conn, "list faculty in YWCC")
    assert r is not None and r.skill == "faculty_in_department"
    assert r.args["org_id"] == 4


def test_returns_none_for_vague_question(conn):
    r = route(conn, "tell me about research")
    assert r is None


# ── new Phase-1 facet routing tests ───────────────────────────────────────────

def test_routes_area_enumeration_to_areas_in_org(conn):
    r = route(conn, "what research areas does Computer Science cover?")
    assert r is not None and r.skill == "areas_in_org" and r.args["org_id"] == 5


def test_routes_area_ranking_to_area_counts(conn):
    r = route(conn, "which research areas have the most faculty in YWCC?")
    assert r is not None and r.skill == "area_counts" and r.args["org_id"] == 4


def test_routes_who_lists_to_people_by_area_tag(conn):
    r = route(conn, "who lists graph as a research area in CS?")
    assert r is not None and r.skill == "people_by_area_tag"
    assert r.args["area"] == "graph" and r.args["org_id"] == 5


def test_who_works_on_still_routes_to_recall_skill(conn):
    # unchanged: "who works on X" must NOT switch to the low-recall tag facet
    r = route(conn, "who works on graph in CS?")
    assert r is not None and r.skill == "people_by_research_area"


def test_faculty_roster_with_areas_routes_to_per_person_areas(conn):
    # "faculty" + "research areas" (no ranking cue) → the per-person areas skill, which lists
    # each professor's ACTUAL areas (and degrades to a names roster + honest 'no areas' line),
    # rather than a bare roster that the LLM would invent research areas for. Not areas_in_org.
    r = route(conn, "show all faculty and their research areas in CS")
    assert r is not None and r.skill == "faculty_areas_in_department" and r.args["org_id"] == 5


def test_area_ranking_with_faculty_metric_still_routes_to_counts(conn):
    # the word 'faculty' here is the ranking metric, not a roster request — _RANK wins.
    r = route(conn, "which research areas have the most faculty in YWCC?")
    assert r is not None and r.skill == "area_counts" and r.args["org_id"] == 4
