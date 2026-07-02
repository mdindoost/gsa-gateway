from __future__ import annotations
import json, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
import pytest
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
from v2.core.retrieval import entity, skills


def _set_attrs(conn, key, **fields):
    row = conn.execute("SELECT attrs FROM nodes WHERE type='Person' AND key=?", (key,)).fetchone()
    attrs = json.loads(row[0]) if row and row[0] else {}
    attrs.update(fields)
    conn.execute("UPDATE nodes SET attrs=? WHERE type='Person' AND key=?", (json.dumps(attrs), key))
    conn.commit()


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    njit = ensure_org(c, "njit", "NJIT", None, type="university")
    ywcc = ensure_org(c, "ywcc", "Ying Wu College of Computing", "njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", "ywcc", type="department")
    ensure_org(c, "acm", "ACM Student Chapter", "njit", type="club")
    ensure_org(c, "wics", "Women in Computing Society", "njit", type="club")
    ensure_org(c, "mtsm", "Martin Tuchman School of Management", "njit", type="college")
    sync_org_nodes(c)
    project_appointment(c, person_key="d/koutis", name="Ioannis Koutis", org_id=cs,
                        category="faculty", titles=["Professor", "Department Chair"],
                        source_section="manual", source="dashboard")
    project_appointment(c, person_key="d/noattr", name="Nadia Noattr", org_id=cs,
                        category="faculty", titles=["Lecturer"], source_section="manual",
                        source="dashboard")
    _set_attrs(c, "d/koutis", email="ik@njit.edu", phone="973-555-0101", office="GITC 4400")
    project_appointment(c, person_key="d/onlyoffice", name="Ola Office", org_id=cs,
                        category="faculty", titles=["Professor"], source_section="manual",
                        source="dashboard")
    _set_attrs(c, "d/onlyoffice", office="GITC 1000")
    c.commit()
    yield c
    c.close()


def test_contact_full(conn):
    r = entity.contact_of_person(conn, "d/koutis")
    assert r["name"] == "Ioannis Koutis"
    assert r["email"] == "ik@njit.edu"
    assert r["phone"] == "973-555-0101"
    assert r["office"] == "GITC 4400"
    assert r["present"] == ["email", "phone", "office"]


def test_contact_partial_office_only(conn):
    r = entity.contact_of_person(conn, "d/onlyoffice")
    assert r["office"] == "GITC 1000"
    assert r["email"] is None and r["phone"] is None
    assert r["present"] == ["office"]


def test_contact_none_on_file(conn):
    r = entity.contact_of_person(conn, "d/noattr")
    assert r["present"] == []
    assert r["email"] is None and r["phone"] is None and r["office"] is None
