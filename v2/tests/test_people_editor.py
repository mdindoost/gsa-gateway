from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import json

import pytest
from v2.core.database.schema import create_all
from v2.core.ingestion.people_editor import (
    add_or_edit_person, remove_person_role, set_person_profiles,
)
from v2.core.retrieval.skills import people_in_org


def _profiles(conn, key):
    raw = conn.execute("SELECT attrs FROM nodes WHERE key=?", (key,)).fetchone()[0]
    return (json.loads(raw) or {}).get("profiles") or {}


def test_set_person_profiles_merges_and_coerces(conn):
    add_or_edit_person(conn, org_id=2, name="Ann Cite", title="Professor", category="faculty")
    key = "dashboard/gsa/ann-cite"
    set_person_profiles(conn, person_key=key, profiles={
        "scholar": {"url": "https://s/x", "citations": "5,021", "h_index": "30"},
        "linkedin": {"url": "https://l/x"},
    })
    conn.commit()
    p = _profiles(conn, key)
    assert p["scholar"]["citations"] == 5021 and isinstance(p["scholar"]["citations"], int)
    assert p["scholar"]["h_index"] == 30
    assert p["linkedin"]["url"] == "https://l/x"

    set_person_profiles(conn, person_key=key, profiles={"scholar": {"i10_index": 62}})
    conn.commit()
    p = _profiles(conn, key)
    assert p["scholar"]["url"] == "https://s/x" and p["scholar"]["citations"] == 5021
    assert p["scholar"]["i10_index"] == 62


def test_set_person_profiles_remove_field(conn):
    add_or_edit_person(conn, org_id=2, name="Bo Gone", title="Professor", category="faculty",
                       profiles={"linkedin": {"url": "https://l/x"}})
    key = "dashboard/gsa/bo-gone"
    conn.commit()
    assert "linkedin" in _profiles(conn, key)
    set_person_profiles(conn, person_key=key, profiles={"linkedin": None})
    conn.commit()
    assert "linkedin" not in _profiles(conn, key)


def test_add_person_with_profiles_and_email_coexist(conn):
    add_or_edit_person(conn, org_id=2, name="Cy Both", title="Professor", category="faculty",
                       email="cy@njit.edu", profiles={"scholar": {"url": "https://s/y"}})
    conn.commit()
    raw = conn.execute("SELECT attrs FROM nodes WHERE key=?",
                       ("dashboard/gsa/cy-both",)).fetchone()[0]
    attrs = json.loads(raw)
    assert attrs["email"] == "cy@njit.edu"
    assert attrs["profiles"]["scholar"]["url"] == "https://s/y"


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
    about = conn.execute("SELECT json_extract(metadata,'$.about') FROM knowledge_items "
                         "WHERE is_active=1 AND json_extract(metadata,'$.entity_id')=?",
                         (res["person_key"],)).fetchone()[0]
    assert about == "Pat runs intramural sports nights for grad students."


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
