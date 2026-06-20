"""faculty_areas_in_department — the anti-fabrication fix for "research areas of the
professors in X". Per-person areas grouped from research_areas items (only people who list
areas); honest names+fallback when nobody does; no-regress on the plain areas_in_org route.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.retrieval import router, skills, structured_answer as SA


def _org(c, oid, name, slug, otype, parent=None):
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(?,?,?,?,?)",
              (oid, parent, name, slug, otype))
    c.execute("INSERT INTO nodes(type,key,name,attrs,source) VALUES('Org',?,?,?,'test')",
              (f"org:{slug}", name, json.dumps({"org_id": oid})))
    return c.execute("SELECT id FROM nodes WHERE key=?", (f"org:{slug}",)).fetchone()[0]


def _person(c, key, name):
    c.execute("INSERT INTO nodes(type,key,name,attrs,source) VALUES('Person',?,?,'{}','crawler')",
              (key, name))
    return c.execute("SELECT id FROM nodes WHERE key=?", (key,)).fetchone()[0]


def _faculty(c, pid, onode):
    c.execute("INSERT INTO edges(src_id,type,dst_id,category,attrs,source) "
              "VALUES(?,'has_role',?,'faculty','{\"titles\":[\"Professor\"]}','crawler')", (pid, onode))


def _areas(c, org_id, entity_id, areas):
    meta = {"entity_id": entity_id, "areas": areas}
    c.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,version,"
              "is_active,created_by) VALUES(?,?,?,?,?,1,1,'crawler')",
              (org_id, "research_areas", f"{entity_id} areas", "x", json.dumps(meta)))


@pytest.fixture
def conn():
    c = create_all(":memory:")
    cs = _org(c, 1, "Computer Science", "computer-science", "department")
    math = _org(c, 2, "Mathematical Sciences", "mathematical-sciences", "department")
    # CS: two list areas, one does not
    gw = _person(c, "p/gw", "Guiling Wang"); _faculty(c, gw, cs)
    _areas(c, 1, "p/gw", ["Applied AI", "Transportation", "applied ai"])   # dup to test dedup
    hp = _person(c, "p/hp", "Hai Phan"); _faculty(c, hp, cs)
    _areas(c, 1, "p/hp", ["Privacy"])
    jw = _person(c, "p/jw", "Jason Wang"); _faculty(c, jw, cs)              # no areas item
    # Math: faculty but NOBODY lists areas
    for k, n in [("p/m1", "Ada Math"), ("p/m2", "Bob Calc")]:
        _faculty(c, _person(c, k, n), math)
    c.commit()
    yield c
    c.close()


def test_skill_groups_per_person_and_dedups(conn):
    rows = skills.faculty_areas_in_department(conn, 1)
    assert [n for n, _ in rows] == ["Guiling Wang", "Hai Phan"]      # only those WITH areas
    gw = dict(rows)["Guiling Wang"]
    assert gw == ["Applied AI", "Transportation"]                   # case-fold dedup, sorted


def test_route_research_areas_of_professors(conn):
    r = router.route(conn, "what are the research areas of professors in computer science?")
    assert r is not None and r.skill == "faculty_areas_in_department" and r.args["org_id"] == 1


def test_format_lists_per_person_areas(conn):
    r = router.route(conn, "research areas of the faculty in computer science")
    out = SA.format_answer(SA.run(conn, r))
    assert out == ("2 of the Computer Science faculty list research areas: "
                   "Guiling Wang — Applied AI, Transportation; Hai Phan — Privacy.")


def test_honest_fallback_when_nobody_lists_areas(conn):
    r = router.route(conn, "research areas of professors in mathematical sciences")
    out = SA.format_answer(SA.run(conn, r))
    assert out.startswith("I don't have research areas listed for Mathematical Sciences's faculty.")
    assert "Ada Math" in out and "Bob Calc" in out                  # honest: name the roster


def test_no_regress_plain_area_enumeration(conn):
    # no faculty cue → still the department area facet, NOT the per-person skill
    r = router.route(conn, "what research areas does computer science cover?")
    assert r is not None and r.skill == "areas_in_org"
