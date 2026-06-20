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
from v2.core.retrieval.entity import research_of_person


def _crawler_areas(conn, pid, org_id, key, name, areas):
    for a in areas:
        anode = upsert_node(conn, type="ResearchArea", key=area_key(a), name=a, source="crawler")
        upsert_edge(conn, src_id=pid, type="researches", dst_id=anode, area_source="structured",
                    source="crawler")
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,version,is_active,"
                 "created_by) VALUES(?,?,?,?,?,1,1,?)",
                 (org_id, "research_areas", name, "Research areas of %s: %s" % (name, "; ".join(areas)),
                  json.dumps({"entity_id": key, "areas": areas,
                              "natural_key": f"{key}:research_areas:main"}), "crawler"))
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


def test_scholar_only_person_gets_areas(db):
    conn, cs, pid = db
    set_person_research_areas(conn, person_key="p/x", areas=["Computing Education", "Pervasive Computing"],
                              org_id=cs)
    conn.commit()
    r = research_of_person(conn, "p/x")
    assert set(r["areas"]) == {"Computing Education", "Pervasive Computing"}


def test_union_dedup_across_sources_casefold(db):
    conn, cs, pid = db
    _crawler_areas(conn, pid, cs, "p/x", "Pat Example", ["Machine Learning"])
    set_person_research_areas(conn, person_key="p/x", areas=["machine learning", "Robotics"], org_id=cs)
    conn.commit()
    r = research_of_person(conn, "p/x")
    # "machine learning" folds into "Machine Learning" (one entry), Robotics added
    assert r["areas"] == sorted(["Machine Learning", "Robotics"], key=str.casefold)
    assert r["areas"].count("Machine Learning") == 1


def test_subsumption_drops_broad_scholar_tag(db):
    conn, cs, pid = db
    _crawler_areas(conn, pid, cs, "p/x", "Pat Example", ["Multimedia Databases"])
    set_person_research_areas(conn, person_key="p/x", areas=["databases"], org_id=cs)
    conn.commit()
    r = research_of_person(conn, "p/x")
    # broad scholar "databases" is a token-subset of crawler "Multimedia Databases" -> dropped from display
    assert r["areas"] == ["Multimedia Databases"]


def test_deterministic_sort(db):
    conn, cs, pid = db
    set_person_research_areas(conn, person_key="p/x", areas=["Zebra Vision", "alpha learning", "Beta"],
                              org_id=cs)
    conn.commit()
    r = research_of_person(conn, "p/x")
    assert r["areas"] == sorted(r["areas"], key=str.casefold)
