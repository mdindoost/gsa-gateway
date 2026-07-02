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
    from v2.core.graph.store import upsert_node
    # Real crawled profiles always get BOTH a Person node (via project_entity) and a KB item
    # sharing the same entity_id/key — mirror that here rather than a KB item floating alone.
    upsert_node(conn, type="Person", key="people.njit.edu/profile/haimin", name="Haimin Wang",
               source="crawler")
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,created_by) "
                 "VALUES(?,'profile','Haimin Wang','...',?,'crawler')",
                 (dep, json.dumps({"entity_id": "people.njit.edu/profile/haimin"})))
    project_appointment(conn, person_key="dashboard/physics/seeded-prof", name="Seeded Prof",
                        org_id=dep, category="faculty", titles=["Professor"],
                        source_section="manual", source="dashboard")
    conn.commit()
    names = [n for n, _ in faculty_in_department(conn, dep)]
    assert "Haimin Wang" in names and "Seeded Prof" in names


def test_faculty_in_department_excludes_non_person_kb_entities(conn):
    # Regression: a department-level document (e.g. a GSA "Ph.D. in X" program-info doc, chunked
    # and filed under this org_id via the dashboard doc-ingest path) is NOT a person and must
    # never surface as a fake "faculty member" — even though it shares the department's org_id
    # with real crawled profiles. Bug: it fell through to the raw entity_id tail as a "name".
    dep = ensure_org(conn, "computer-science", "Computer Science", parent_slug="njit",
                     type="department")
    sync_org_nodes(conn)
    import json
    from v2.core.graph.store import upsert_node
    upsert_node(conn, type="Person", key="people.njit.edu/profile/borcea", name="Cristian Borcea",
               source="crawler")
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,created_by) "
                 "VALUES(?,'profile','Cristian Borcea','...',?,'crawler')",
                 (dep, json.dumps({"entity_id": "people.njit.edu/profile/borcea"})))
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,created_by) "
                 "VALUES(?,'policy','Ph.D. in Computer Science','...',?,'dashboard')",
                 (dep, json.dumps({"entity_id": "gsa-doc/phd-computer-science#0"})))
    conn.commit()
    fac = faculty_in_department(conn, dep)
    names = [n for n, _ in fac]
    ids = [e for _, e in fac]
    assert "Cristian Borcea" in names
    assert "phd-computer-science#0" not in names
    assert "gsa-doc/phd-computer-science#0" not in ids
