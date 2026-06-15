from __future__ import annotations
import json, sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.graph.project import project_appointment
from scripts.verify_kg import verify_kg


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(4,1,'YWCC','ywcc','college')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(5,4,'Computer Science','computer-science','department')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(7,4,'Informatics','informatics','department')")
    c.commit()
    yield c
    c.close()


def _kb(c, org, eid):
    c.execute("INSERT INTO knowledge_items(org_id,type,content,metadata,created_by) "
              "VALUES(?,?,?,?,'crawler')", (org, "profile", "x", json.dumps({"entity_id": eid})))


def test_aligned_returns_no_issues(conn):
    project_appointment(conn, person_key="p/a", name="A", org_id=5, category="faculty",
                        titles=[], source_section="Professors")
    _kb(conn, 5, "p/a"); conn.commit()
    assert verify_kg(conn) == []


def test_detects_misfiled_kb(conn):
    project_appointment(conn, person_key="p/a", name="A", org_id=7, category="faculty",
                        titles=[], source_section="Assistant Professors")   # appointed Informatics
    _kb(conn, 5, "p/a"); conn.commit()                                       # KB under CS (wrong)
    assert any("mis-filed" in i for i in verify_kg(conn))


def test_detects_missing_kb(conn):
    project_appointment(conn, person_key="p/b", name="B", org_id=5, category="faculty",
                        titles=[], source_section="Professors")             # appointment, no KB
    conn.commit()
    assert any("no KB content" in i for i in verify_kg(conn))
