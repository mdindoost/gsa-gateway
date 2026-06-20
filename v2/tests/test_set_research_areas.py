from __future__ import annotations
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment, area_key
from v2.core.graph.store import upsert_node, upsert_edge
from v2.core.ingestion.people_editor import set_person_research_areas


def _crawler_area(conn, pid, org_id, person_key, name, area):
    """Simulate a crawler-created research area (node + edge + KB item) for isolation tests."""
    anode = upsert_node(conn, type="ResearchArea", key=area_key(area), name=area, source="crawler")
    upsert_edge(conn, src_id=pid, type="researches", dst_id=anode, area_source="structured",
                source="crawler")
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,version,is_active,"
                 "created_by) VALUES(?,?,?,?,?,1,1,?)",
                 (org_id, "research_areas", name,
                  f"Research areas of {name}: {area}",
                  json.dumps({"entity_id": person_key, "areas": [area],
                              "natural_key": f"{person_key}:research_areas:main"}), "crawler"))
    conn.commit()


@pytest.fixture()
def db():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    cs = ensure_org(c, "cs", "Computer Science", parent_slug="njit", type="department")
    sync_org_nodes(c)
    project_appointment(c, person_key="p/x", name="Pat Example", org_id=cs, category="faculty",
                        titles=["Professor"], source_section="manual", source="dashboard")
    pid = c.execute("SELECT id FROM nodes WHERE key='p/x'").fetchone()[0]
    c.commit()
    yield c, cs, pid
    c.close()


def _scholar_areas(conn, key):
    r = conn.execute("SELECT json_extract(metadata,'$.areas'), json_extract(metadata,'$.area_source'),"
                     " org_id FROM knowledge_items WHERE is_active=1 AND type='research_areas' "
                     "AND created_by='scholar' AND json_extract(metadata,'$.entity_id')=?", (key,)).fetchone()
    return r


def test_writes_node_edge_and_kb_item(db):
    conn, cs, pid = db
    set_person_research_areas(conn, person_key="p/x", areas=["Machine Learning", "Robotics"],
                              org_id=cs)
    conn.commit()
    # edge (source scholar, area_source external)
    rows = conn.execute("SELECT a.name FROM edges e JOIN nodes a ON a.id=e.dst_id "
                        "WHERE e.src_id=? AND e.type='researches' AND e.is_active=1 AND e.source='scholar' "
                        "AND e.area_source='external'", (pid,)).fetchall()
    assert {r[0] for r in rows} == {"Machine Learning", "Robotics"}
    # KB item (created_by scholar, distinct natural_key, area_source, correct org)
    areas, asrc, org = _scholar_areas(conn, "p/x")
    assert json.loads(areas) == ["Machine Learning", "Robotics"]
    assert asrc == "scholar" and org == cs


def test_reuses_existing_area_node_casefold(db):
    conn, cs, pid = db
    _crawler_area(conn, pid, cs, "p/x", "Pat Example", "machine learning")
    n_before = conn.execute("SELECT COUNT(*) FROM nodes WHERE type='ResearchArea'").fetchone()[0]
    set_person_research_areas(conn, person_key="p/x", areas=["Machine Learning"], org_id=cs)
    conn.commit()
    # "Machine Learning" folds into the existing "machine learning" node — no new node
    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE type='ResearchArea'").fetchone()[0] == n_before
    # and the crawler edge is not duplicated (one researches edge to that node)
    nid = conn.execute("SELECT id FROM nodes WHERE type='ResearchArea' AND key='machine learning'").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM edges WHERE src_id=? AND dst_id=? AND type='researches' "
                        "AND is_active=1", (pid, nid)).fetchone()[0] == 1


def test_does_not_touch_crawler_kb_item(db):
    conn, cs, pid = db
    _crawler_area(conn, pid, cs, "p/x", "Pat Example", "Data Mining")
    set_person_research_areas(conn, person_key="p/x", areas=["Robotics"], org_id=cs)
    conn.commit()
    # crawler research_areas item still active
    assert conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND type='research_areas' "
                        "AND created_by='crawler' AND json_extract(metadata,'$.entity_id')='p/x'").fetchone()[0] == 1


def test_second_call_replaces_no_dup(db):
    conn, cs, pid = db
    set_person_research_areas(conn, person_key="p/x", areas=["Machine Learning", "Robotics"], org_id=cs)
    conn.commit()
    set_person_research_areas(conn, person_key="p/x", areas=["Robotics"], org_id=cs)
    conn.commit()
    # exactly one active scholar KB item; areas updated
    assert conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND type='research_areas' "
                        "AND created_by='scholar' AND json_extract(metadata,'$.entity_id')='p/x'").fetchone()[0] == 1
    assert json.loads(_scholar_areas(conn, "p/x")[0]) == ["Robotics"]
    # the dropped "Machine Learning" scholar edge is deactivated
    active = conn.execute("SELECT a.name FROM edges e JOIN nodes a ON a.id=e.dst_id WHERE e.src_id=? "
                          "AND e.type='researches' AND e.is_active=1 AND e.source='scholar'", (pid,)).fetchall()
    assert {r[0] for r in active} == {"Robotics"}
