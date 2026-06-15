from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.local_server import GatewayHandler


@pytest.fixture()
def conn():
    from v2.core.database.schema import create_all
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(2,1,'GSA','gsa','custom')")
    c.commit()
    yield c
    c.close()


def test_post_person_and_remove(conn):
    # handler methods only use (conn, b); call them unbound with self=None
    res = GatewayHandler._post_person(None, conn, {
        "org_id": 2, "name": "Pat Sport", "title": "Sport Officer",
        "role_type": "Officer", "email": "pat@njit.edu", "about": "runs sports"})
    assert res["success"] and res["person_key"] == "dashboard/gsa/pat-sport"
    assert res["needs_reindex"] is True
    from v2.core.retrieval.skills import people_in_org
    assert ("Pat Sport", "Sport Officer", "pat@njit.edu") in people_in_org(conn, 2)

    rem = GatewayHandler._post_person_remove(None, conn, {
        "person_key": "dashboard/gsa/pat-sport", "org_id": 2})
    assert rem["success"] and rem["removed"] is True
    assert people_in_org(conn, 2) == []
