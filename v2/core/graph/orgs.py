"""The org bridge: `organizations` is the one authoritative tree; Org nodes only
reference it (key=slug, attrs.org_id) and `part_of` is derived from parent_id."""
from __future__ import annotations

import sqlite3

from v2.core.graph.store import upsert_edge, upsert_node


def org_node_id(conn: sqlite3.Connection, org_id: int) -> int:
    """Get/create the Org node that bridges ``organizations.id``."""
    o = conn.execute("SELECT id,name,slug FROM organizations WHERE id=?", (org_id,)).fetchone()
    if not o:
        raise ValueError(f"no organization id={org_id}")
    return upsert_node(conn, type="Org", key=o["slug"], name=o["name"],
                       attrs={"org_id": o["id"]})


def sync_org_nodes(conn: sqlite3.Connection) -> None:
    """Project every active organization to an Org node and a `part_of` edge to its parent."""
    rows = conn.execute(
        "SELECT id, parent_id FROM organizations WHERE is_active=1").fetchall()
    for o in rows:
        org_node_id(conn, o["id"])
    for o in rows:
        if o["parent_id"]:
            upsert_edge(conn, src_id=org_node_id(conn, o["id"]), type="part_of",
                        dst_id=org_node_id(conn, o["parent_id"]))
