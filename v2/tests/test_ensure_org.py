from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(4,'Ying Wu College of Computing','ywcc','college')")
    c.commit()
    yield c
    c.close()


def test_ensure_org_creates_with_parent_and_is_idempotent(conn):
    ywcc = conn.execute("SELECT id FROM organizations WHERE slug='ywcc'").fetchone()[0]
    new_id = ensure_org(conn, "college-administration", "College Administration", "ywcc", "admin-unit")
    row = conn.execute(
        "SELECT parent_id FROM organizations WHERE id=?", (new_id,)).fetchone()
    assert row[0] == ywcc
    # idempotent: same slug -> same id
    again = ensure_org(conn, "college-administration", "College Administration", "ywcc", "admin-unit")
    assert again == new_id
