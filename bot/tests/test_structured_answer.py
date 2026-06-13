"""Tests for the structured-answer executor + formatter (deterministic, no LLM).

The formatter's output is BOTH the LLM grounding context and the fallback answer, so
it must be a complete, correct, standalone answer on its own.
"""

import json

import pytest

from v2.core.database.schema import create_all
from v2.core.retrieval.router import Route
from v2.core.retrieval.structured_answer import format_answer, run


@pytest.fixture
def conn(tmp_path):
    c = create_all(str(tmp_path / "t.db"))
    for oid, parent, name, slug, typ in [
        (1, None, "NJIT", "njit", "university"),
        (4, 1, "Ying Wu College of Computing", "ywcc", "college"),
        (5, 4, "Computer Science", "computer-science", "department"),
        (6, 4, "Data Science", "data-science", "department"),
    ]:
        c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(?,?,?,?,?)",
                  (oid, parent, name, slug, typ))

    def add(org, eid, name, typ, content):
        c.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata) VALUES(?,?,?,?,?)",
                  (org, typ, name if typ == "profile" else f"{name} — {typ}", content,
                   json.dumps({"entity_id": eid})))
    add(5, "p/koutis", "Ioannis Koutis", "profile", "x")
    add(5, "p/koutis", "Ioannis Koutis", "research_areas", "graph algorithms")
    add(6, "p/bader", "David Bader", "profile", "x")
    add(6, "p/bader", "David Bader", "research_statement", "graph analytics")
    c.commit()
    yield c
    c.close()


def test_org_departments_answer(conn):
    txt = format_answer(run(conn, Route("org_departments", {"org_id": 4})))
    assert "Computer Science" in txt and "Data Science" in txt and "2 department" in txt


def test_people_by_research_area_answer_is_complete(conn):
    txt = format_answer(run(conn, Route("people_by_research_area", {"area": "graph", "org_id": 4})))
    assert "David Bader" in txt and "Ioannis Koutis" in txt and "graph" in txt
    assert "2 faculty" in txt


def test_count_answer(conn):
    txt = format_answer(run(conn, Route("count_people_by_research_area", {"area": "graph", "org_id": 4})))
    assert txt.startswith("2 faculty")


def test_empty_research_area_is_honest_not_guessed(conn):
    txt = format_answer(run(conn, Route("people_by_research_area", {"area": "quantum", "org_id": 4})))
    assert "couldn't find" in txt.lower() and "quantum" in txt


def test_faculty_in_department_answer(conn):
    txt = format_answer(run(conn, Route("faculty_in_department", {"org_id": 5})))
    assert "Ioannis Koutis" in txt and "1 faculty" in txt
