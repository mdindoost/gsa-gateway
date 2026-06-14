from __future__ import annotations
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.graph.orgs import org_node_id, sync_org_nodes


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(4,'YWCC','ywcc','college')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(5,4,'Computer Science','computer-science','department')")
    c.commit()
    yield c
    c.close()


def test_org_node_bridges_organizations_id(conn):
    nid = org_node_id(conn, 5)
    row = conn.execute("SELECT type,key,name,attrs FROM nodes WHERE id=?", (nid,)).fetchone()
    assert row[0] == "Org" and row[1] == "computer-science" and row[2] == "Computer Science"
    assert json.loads(row[3])["org_id"] == 5


def test_sync_builds_part_of_from_parent_id(conn):
    sync_org_nodes(conn)
    cs = org_node_id(conn, 5)
    ywcc = org_node_id(conn, 4)
    e = conn.execute("SELECT id FROM edges WHERE src_id=? AND type='part_of' AND dst_id=?",
                     (cs, ywcc)).fetchone()
    assert e is not None
