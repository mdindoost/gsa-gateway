"""people_in_org must NOT resolve against the university ROOT org (flip-gate fix, 2026-06-22).

"people at njit" enumerates only the roles attached DIRECTLY to the root node (the President) —
a thin, misleading answer that today's RAG (and the v2.1 flip) would otherwise surface as a
confident structured fact. A bare people-enumeration on the root must fall through to RAG; a
real sub-org (department/club) still resolves to people_in_org as before."""
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
              "VALUES(2,1,'Graduate Student Association','gsa','club')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(3,1,'Ying Wu College of Computing','ywcc','college')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(4,3,'Computer Science','computer-science','department')")
    apply_org_aliases(c)
    c.commit()
    yield c
    c.close()


@pytest.mark.parametrize("q", [
    "top graph people at njit",
    "who are the people at njit",
])
def test_people_in_org_root_falls_through(conn, q):
    assert route(conn, q) is None


@pytest.mark.parametrize("q,oid", [
    ("who are the people in gsa", 2),
    ("who are the people in computer science", 4),
])
def test_people_in_org_suborg_still_resolves(conn, q, oid):
    r = route(conn, q)
    assert r is not None and r.skill == "people_in_org" and r.args["org_id"] == oid
