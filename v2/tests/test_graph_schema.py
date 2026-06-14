from __future__ import annotations
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    yield c
    c.close()


def test_graph_tables_exist(conn):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"raw_pages", "nodes", "edges"} <= names


def test_edges_category_check_rejects_bad_value(conn):
    conn.execute("INSERT INTO nodes(type,key,name,source) VALUES('Person','p/a','A','crawler')")
    conn.execute("INSERT INTO nodes(type,key,name,source) VALUES('Org','cs','CS','crawler')")
    a = conn.execute("SELECT id FROM nodes WHERE key='p/a'").fetchone()[0]
    o = conn.execute("SELECT id FROM nodes WHERE key='cs'").fetchone()[0]
    with pytest.raises(Exception):
        conn.execute("INSERT INTO edges(src_id,type,dst_id,category,source) "
                     "VALUES(?,?,?,?,?)", (a, "has_role", o, "president", "crawler"))


def test_node_key_is_unique_per_type(conn):
    conn.execute("INSERT INTO nodes(type,key,name,source) VALUES('Person','p/a','A','crawler')")
    with pytest.raises(Exception):
        conn.execute("INSERT INTO nodes(type,key,name,source) VALUES('Person','p/a','A2','crawler')")
