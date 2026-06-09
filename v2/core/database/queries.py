"""Common v2 queries — settings resolution with org-tree inheritance.

A setting is looked up on the post's org first, then walked up the parent chain
to the root. This lets a university set defaults once at the root and have every
college/department/club inherit them, while any node can override locally.
"""

from __future__ import annotations

import json
import sqlite3


def org_ancestors(conn: sqlite3.Connection, org_id: int) -> list[int]:
    """Return [org_id, parent, grandparent, …, root] nearest-first."""
    rows = conn.execute(
        "WITH RECURSIVE up(id, parent_id, depth) AS ("
        "  SELECT id, parent_id, 0 FROM organizations WHERE id=? "
        "  UNION ALL "
        "  SELECT o.id, o.parent_id, up.depth+1 FROM organizations o JOIN up ON o.id=up.parent_id"
        ") SELECT id FROM up ORDER BY depth",
        (org_id,),
    ).fetchall()
    return [r["id"] for r in rows]


def _setting_row(conn, org_id, key, inherit):
    if not inherit:
        return conn.execute(
            "SELECT value, type FROM settings WHERE org_id=? AND key=?", (org_id, key)
        ).fetchone()
    for oid in org_ancestors(conn, org_id):
        row = conn.execute(
            "SELECT value, type FROM settings WHERE org_id=? AND key=?", (oid, key)
        ).fetchone()
        if row:
            return row
    return None


def _coerce(value, vtype):
    if value is None:
        return None
    if vtype == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if vtype == "bool":
        return str(value).strip().lower() in ("true", "1", "yes", "on")
    if vtype == "json":
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def get_setting(conn, org_id, key, default=None, inherit=True):
    """Raw string value (with inheritance), or ``default``."""
    row = _setting_row(conn, org_id, key, inherit)
    return row["value"] if row else default


def get_setting_typed(conn, org_id, key, default=None, inherit=True):
    """Value parsed per the setting's ``type`` column, or ``default``."""
    row = _setting_row(conn, org_id, key, inherit)
    return _coerce(row["value"], row["type"]) if row else default
