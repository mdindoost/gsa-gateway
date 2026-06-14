from __future__ import annotations
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.graph.store import (
    active_edge_ids_from, deactivate_edges, upsert_edge, upsert_node)


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    yield c
    c.close()


def test_upsert_node_is_idempotent_and_updates(conn):
    n1 = upsert_node(conn, type="Person", key="p/a", name="Ann", attrs={"email": "a@x"})
    n2 = upsert_node(conn, type="Person", key="p/a", name="Ann B", attrs={"email": "b@x"})
    assert n1 == n2
    row = conn.execute("SELECT name, attrs FROM nodes WHERE id=?", (n1,)).fetchone()
    assert row[0] == "Ann B" and '"email": "b@x"' in row[1]


def test_upsert_edge_idempotent_and_active_set(conn):
    p = upsert_node(conn, type="Person", key="p/a", name="Ann")
    o = upsert_node(conn, type="Org", key="cs", name="CS", attrs={"org_id": 5})
    e1 = upsert_edge(conn, src_id=p, type="has_role", dst_id=o, category="faculty")
    e2 = upsert_edge(conn, src_id=p, type="has_role", dst_id=o, category="faculty")
    assert e1 == e2
    assert active_edge_ids_from(conn, p) == {e1}


def test_deactivate_edges(conn):
    p = upsert_node(conn, type="Person", key="p/a", name="Ann")
    o = upsert_node(conn, type="Org", key="cs", name="CS")
    e = upsert_edge(conn, src_id=p, type="has_role", dst_id=o, category="faculty")
    deactivate_edges(conn, {e})
    assert active_edge_ids_from(conn, p) == set()
