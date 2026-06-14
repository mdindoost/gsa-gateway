from __future__ import annotations
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.graph.project import category_from_titles, project_entity
from v2.core.graph.store import active_edge_ids_from
from v2.core.ingestion.entity import EntityRecord


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(5,'Computer Science','computer-science','department')")
    c.commit()
    yield c
    c.close()


def test_category_from_titles():
    assert category_from_titles(["Associate Professor, Computer Science"]) == "faculty"
    assert category_from_titles(["Professor", "Associate Dean for Academic Affairs"]) == "faculty"
    assert category_from_titles(["Dean, Ying Wu College of Computing"]) == "admin"
    assert category_from_titles(["Director of Marketing and Communications"]) == "staff"


def _rec(areas):
    return EntityRecord(entity_id="p/ikoutis", name="Ioannis Koutis", org="Computer Science",
                        titles=["Associate Professor, Computer Science"],
                        research_areas=areas,
                        contact={"email": "ioannis.koutis@njit.edu", "office": "4105 GITC"})


def test_project_creates_person_role_and_research_edges(conn):
    pid = project_entity(conn, _rec(["Spectral graph theory", "Graph sparsification"]), 5)
    hr = conn.execute("SELECT category FROM edges WHERE src_id=? AND type='has_role'", (pid,)).fetchall()
    assert [r[0] for r in hr] == ["faculty"]
    rs = conn.execute("SELECT area_source FROM edges WHERE src_id=? AND type='researches' "
                      "AND is_active=1", (pid,)).fetchall()
    assert len(rs) == 2 and all(r[0] == "structured" for r in rs)
    attrs = conn.execute("SELECT attrs FROM nodes WHERE id=?", (pid,)).fetchone()[0]
    assert "ioannis.koutis@njit.edu" in attrs and "4105 GITC" in attrs


def test_reproject_with_dropped_area_deactivates_stale_edge(conn):
    pid = project_entity(conn, _rec(["Spectral graph theory", "Graph sparsification"]), 5)
    before = active_edge_ids_from(conn, pid, type="researches")
    assert len(before) == 2
    project_entity(conn, _rec(["Spectral graph theory"]), 5)
    after = active_edge_ids_from(conn, pid, type="researches")
    assert len(after) == 1
