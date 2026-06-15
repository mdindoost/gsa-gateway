from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from v2.core.retrieval.router import route


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(2,1,'Graduate Student Association','gsa','custom')")
    c.commit()
    yield c
    c.close()


def test_who_are_the_gsa_officers_routes(conn):
    r = route(conn, "who are the GSA officers?")
    assert r is not None and r.skill == "officers_in_org" and r.args["org_id"] == 2


def test_who_is_the_gsa_president_routes(conn):
    r = route(conn, "who is the GSA president")
    assert r is not None and r.skill == "officers_in_org" and r.args["org_id"] == 2


def test_descriptive_question_still_falls_through(conn):
    assert route(conn, "what is the meaning of graduate research day") is None
