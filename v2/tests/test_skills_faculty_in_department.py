from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
from v2.core.retrieval.skills import faculty_in_department


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.commit()
    yield c
    c.close()


def test_faculty_in_department_includes_graph_only_people_with_proper_names(conn):
    # A department whose people were SEEDED into the graph (source='dashboard') with NO KB items
    # — like Theatre, whose page lacks the profile template. They must still appear, by name.
    dep = ensure_org(conn, "theater-arts-technology", "Theater Arts & Technology",
                     parent_slug="njit", type="department")
    sync_org_nodes(conn)
    project_appointment(conn, person_key="dashboard/theater-arts-technology/emily-edwards",
                        name="Emily Edwards", org_id=dep, category="faculty",
                        titles=["University Lecturer"], source_section="manual", source="dashboard")
    project_appointment(conn, person_key="dashboard/theater-arts-technology/janelle-z",
                        name="Janelle Zapata Castellano", org_id=dep, category="staff",
                        titles=["Administrative Assistant"], source_section="manual", source="dashboard")
    conn.commit()
    fac = faculty_in_department(conn, dep)
    names = [n for n, _ in fac]
    assert "Emily Edwards" in names            # graph-only faculty appears, by display name
    assert "Janelle Zapata Castellano" not in names   # staff is not faculty


def test_faculty_in_department_unions_kb_and_graph(conn):
    # KB-derived (crawled) + graph-derived (seeded) both included, deduped.
    dep = ensure_org(conn, "physics", "Physics", parent_slug="njit", type="department")
    sync_org_nodes(conn)
    import json
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,created_by) "
                 "VALUES(?,'profile','Haimin Wang','...',?,'crawler')",
                 (dep, json.dumps({"entity_id": "people.njit.edu/profile/haimin"})))
    project_appointment(conn, person_key="dashboard/physics/seeded-prof", name="Seeded Prof",
                        org_id=dep, category="faculty", titles=["Professor"],
                        source_section="manual", source="dashboard")
    conn.commit()
    names = [n for n, _ in faculty_in_department(conn, dep)]
    assert "Haimin Wang" in names and "Seeded Prof" in names
