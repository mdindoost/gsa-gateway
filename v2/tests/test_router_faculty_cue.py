"""Faculty-roster routing (multi-college expansion, 2026-06-19).

Department official names carry '&'/extra words, and people ask for faculty many ways
('who teaches in X', 'lecturers in X', 'academic staff in X'). All of these + a resolved org
must route to the complete structured faculty_in_department list — not partial RAG, not the
generic people_in_org list."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.ingestion.entry_points import apply_org_aliases
from v2.core.retrieval.router import route


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(2,1,'Newark College of Engineering','nce','college')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(3,2,'Civil & Environmental Engineering','civil-environmental-engineering','department')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(4,2,'Mechanical & Industrial Engineering','mechanical-industrial-engineering','department')")
    apply_org_aliases(c)        # so 'civil engineering' / 'mechanical engineering' resolve
    c.commit()
    yield c
    c.close()


@pytest.mark.parametrize("q", [
    "who are the faculty in civil engineering",
    "who teaches in mechanical engineering",
    "who teaches civil engineering",
    "professors in civil and environmental engineering",
    "lecturers in mechanical engineering",
    "instructors in civil engineering",
    "academic staff in mechanical engineering",
    "mechanical engineering faculty",
])
def test_faculty_phrasings_route_to_faculty_in_department(conn, q):
    r = route(conn, q)
    assert r is not None and r.skill == "faculty_in_department", f"{q!r} -> {r}"


def test_short_dept_name_resolves_via_alias(conn):
    # 'civil engineering' must reach the Civil & Environmental Engineering org (id 3)
    r = route(conn, "who are the faculty in civil engineering")
    assert r.args["org_id"] == 3


def test_generic_people_query_is_not_hijacked(conn):
    # 'people in X' is NOT a faculty cue → stays people_in_org
    r = route(conn, "who are the people in civil engineering")
    assert r is not None and r.skill == "people_in_org"
