"""Tests for the deterministic structured-query router (v2/core/retrieval/router.py).

The router classifies a question to (skill, resolved args) ONLY when it is clearly
structured; otherwise returns None → the question falls through to semantic RAG. The
dangerous case is a *descriptive* question forced into a skill (false positive), so
those must return None.
"""

import json

import pytest

from v2.core.database.schema import create_all
from v2.core.retrieval.router import route


@pytest.fixture
def conn(tmp_path):
    c = create_all(str(tmp_path / "t.db"))
    for oid, parent, name, slug, typ in [
        (1, None, "New Jersey Institute of Technology", "njit", "university"),
        (4, 1, "Ying Wu College of Computing", "ywcc", "college"),
        (5, 4, "Computer Science", "computer-science", "department"),
        (6, 4, "Data Science", "data-science", "department"),
    ]:
        c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(?,?,?,?,?)",
                  (oid, parent, name, slug, typ))
    c.commit()
    yield c
    c.close()


# ── structured → routed ───────────────────────────────────────────────────────

def test_departments_in_org(conn):
    r = route(conn, "which departments are in YWCC?")
    assert r is not None and r.skill == "org_departments" and r.args["org_id"] == 4


def test_departments_phrasing_variant(conn):
    r = route(conn, "what departments does the Ying Wu College of Computing have")
    assert r and r.skill == "org_departments" and r.args["org_id"] == 4


def test_who_works_on_area_with_org_scope(conn):
    r = route(conn, "who works on graph in YWCC")
    assert r and r.skill == "people_by_research_area"
    assert r.args["area"] == "graph" and r.args["org_id"] == 4


def test_who_researches_area_no_scope(conn):
    r = route(conn, "who researches machine learning")
    assert r and r.skill == "people_by_research_area"
    assert r.args["area"] == "machine learning" and r.args["org_id"] is None


def test_how_many_work_on_area(conn):
    r = route(conn, "how many faculty work on security")
    assert r and r.skill == "count_people_by_research_area" and r.args["area"] == "security"


def test_list_faculty_in_department(conn):
    r = route(conn, "list all CS faculty")
    assert r and r.skill == "faculty_in_department" and r.args["org_id"] == 5


def test_faculty_in_department_variant(conn):
    r = route(conn, "faculty in Data Science")
    assert r and r.skill == "faculty_in_department" and r.args["org_id"] == 6


# ── descriptive / ambiguous → None (semantic RAG) ─────────────────────────────

@pytest.mark.parametrize("q", [
    "tell me about Ioannis Koutis",
    "what is the GSA",
    "explain the funding process",
    "how do I register for an event",
    "who is the dean",                       # single-person descriptive, no skill match
    "what research does graph theory involve",  # not an enumerate/filter ask
])
def test_descriptive_questions_are_not_routed(conn, q):
    assert route(conn, q) is None


def test_unresolved_org_falls_through(conn):
    # department query naming an org we don't have → no safe query → None
    assert route(conn, "which departments are in the College of Medicine") is None
