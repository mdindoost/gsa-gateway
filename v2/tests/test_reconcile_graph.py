from __future__ import annotations
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.graph.store import active_edge_ids_from
from v2.core.ingestion.decompose import decompose
from v2.core.ingestion.entity import EntityRecord
from v2.core.ingestion.reconcile import reconcile_entity


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(5,'Computer Science','computer-science','department')")
    c.commit()
    yield c
    c.close()


def _rec(areas):
    return EntityRecord(entity_id="p/ikoutis", name="Ioannis Koutis", org="Computer Science",
                        source_url="https://people.njit.edu/profile/ikoutis",
                        titles=["Associate Professor, Computer Science"],
                        research_areas=areas)


def test_reconcile_populates_graph_in_same_call(conn):
    rec = _rec(["Spectral graph theory", "Graph sparsification"])
    reconcile_entity(conn, 5, rec.entity_id, decompose(rec), rec=rec)
    pid = conn.execute("SELECT id FROM nodes WHERE type='Person' AND key='p/ikoutis'").fetchone()
    assert pid is not None
    n = conn.execute("SELECT COUNT(*) FROM edges WHERE type='researches' AND is_active=1").fetchone()[0]
    assert n == 2


def test_text_and_graph_stay_consistent_on_dropped_area(conn):
    rec2 = _rec(["Spectral graph theory", "Graph sparsification"])
    reconcile_entity(conn, 5, rec2.entity_id, decompose(rec2), rec=rec2)
    rec1 = _rec(["Spectral graph theory"])
    reconcile_entity(conn, 5, rec1.entity_id, decompose(rec1), rec=rec1)
    pid = conn.execute("SELECT id FROM nodes WHERE key='p/ikoutis'").fetchone()[0]
    assert len(active_edge_ids_from(conn, pid, type="researches")) == 1
    ra = conn.execute("SELECT content FROM knowledge_items WHERE type='research_areas' "
                      "AND is_active=1 AND json_extract(metadata,'$.entity_id')='p/ikoutis'").fetchone()
    assert "Graph sparsification" not in ra[0]
