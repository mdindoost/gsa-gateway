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


def test_phase1b_tables_exist(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"frontier", "page_nodes"} <= names


def test_frontier_status_check_rejects_bad_value(conn):
    with pytest.raises(Exception):
        conn.execute("INSERT INTO frontier(url, status) VALUES('http://x', 'banana')")


def test_frontier_dedup_on_from_node_and_url(conn):
    conn.execute("INSERT INTO nodes(type,key,name,source) VALUES('Person','p/a','A','crawler')")
    n = conn.execute("SELECT id FROM nodes WHERE key='p/a'").fetchone()[0]
    conn.execute("INSERT INTO frontier(from_node_id,url) VALUES(?, 'http://x')", (n,))
    with pytest.raises(Exception):
        conn.execute("INSERT INTO frontier(from_node_id,url) VALUES(?, 'http://x')", (n,))


def test_frontier_root_dedup_on_null_from_node(conn):
    # root entry points (from_node_id NULL) dedup by url via the partial unique index
    conn.execute("INSERT INTO frontier(from_node_id,url) VALUES(NULL, 'http://root')")
    with pytest.raises(Exception):
        conn.execute("INSERT INTO frontier(from_node_id,url) VALUES(NULL, 'http://root')")


def test_page_nodes_links_raw_to_node(conn):
    conn.execute("INSERT INTO raw_pages(url,content,struct_hash,status) VALUES('http://p','x','h','ok')")
    conn.execute("INSERT INTO nodes(type,key,name,source) VALUES('Person','p/a','A','crawler')")
    n = conn.execute("SELECT id FROM nodes WHERE key='p/a'").fetchone()[0]
    conn.execute("INSERT INTO page_nodes(raw_url,node_id) VALUES('http://p',?)", (n,))
    assert conn.execute("SELECT COUNT(*) FROM page_nodes").fetchone()[0] == 1
    # FK: a node_id that doesn't exist is rejected
    with pytest.raises(Exception):
        conn.execute("INSERT INTO page_nodes(raw_url,node_id) VALUES('http://p', 99999)")
