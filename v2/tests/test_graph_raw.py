from __future__ import annotations
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.graph.raw import save_raw_page, struct_hash


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    yield c
    c.close()


def test_struct_hash_ignores_byte_noise_outside_structure():
    a = "<html><body><div>Areas: graph</div><!-- nonce 111 --></body></html>"
    b = "<html><body><div>Areas: graph</div><!-- nonce 999 --></body></html>"
    assert struct_hash(a) == struct_hash(b)


def test_struct_hash_changes_when_content_changes():
    a = "<html><body><div>Areas: graph</div></body></html>"
    b = "<html><body><div>Areas: graphs and trees</div></body></html>"
    assert struct_hash(a) != struct_hash(b)


def test_save_raw_page_upserts(conn):
    save_raw_page(conn, "http://x/p", "<html><body>one</body></html>")
    save_raw_page(conn, "http://x/p", "<html><body>two</body></html>")
    rows = conn.execute("SELECT content FROM raw_pages WHERE url='http://x/p'").fetchall()
    assert len(rows) == 1 and "two" in rows[0][0]
