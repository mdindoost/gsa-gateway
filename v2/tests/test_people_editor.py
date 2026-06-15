from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from v2.core.ingestion.people_editor import add_or_edit_person, remove_person_role
from v2.core.retrieval.skills import people_in_org


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(2,1,'GSA','gsa','custom')")
    c.commit()
    yield c
    c.close()


def test_add_person_creates_graph_and_bio(conn):
    res = add_or_edit_person(conn, org_id=2, name="Pat Sport", title="Sport Officer",
                             category="officer", email="pat@njit.edu",
                             about="Pat runs intramural sports nights for grad students.")
    conn.commit()
    assert res["person_key"] == "dashboard/gsa/pat-sport"
    assert ("Pat Sport", "Sport Officer", "pat@njit.edu") in people_in_org(conn, 2)
    n = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND created_by='dashboard' "
                     "AND json_extract(metadata,'$.entity_id')=?", (res["person_key"],)).fetchone()[0]
    assert n == 1


def test_edit_person_is_idempotent_and_updates(conn):
    add_or_edit_person(conn, org_id=2, name="Pat Sport", title="Sport Officer",
                       category="officer", email="pat@njit.edu", about="v1")
    add_or_edit_person(conn, org_id=2, name="Pat Sport", title="Sports & Wellness Officer",
                       category="officer", email="pat2@njit.edu", about="v2")
    conn.commit()
    people = people_in_org(conn, 2)
    assert [p for p in people if p[0] == "Pat Sport"] == [("Pat Sport", "Sports & Wellness Officer", "pat2@njit.edu")]
    n = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 "
                     "AND json_extract(metadata,'$.entity_id')='dashboard/gsa/pat-sport'").fetchone()[0]
    assert n == 1


def test_remove_person_role_soft_deletes(conn):
    add_or_edit_person(conn, org_id=2, name="Pat Sport", title="Sport Officer",
                       category="officer", email=None, about="bio")
    conn.commit()
    res = remove_person_role(conn, person_key="dashboard/gsa/pat-sport", org_id=2)
    conn.commit()
    assert res["removed"] is True and res["person_deactivated"] is True
    assert people_in_org(conn, 2) == []
    active = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 "
                          "AND json_extract(metadata,'$.entity_id')='dashboard/gsa/pat-sport'").fetchone()[0]
    kept = conn.execute("SELECT COUNT(*) FROM knowledge_items "
                        "WHERE json_extract(metadata,'$.entity_id')='dashboard/gsa/pat-sport'").fetchone()[0]
    assert active == 0 and kept == 1
