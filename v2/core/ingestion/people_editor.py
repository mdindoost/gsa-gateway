"""Single-person manual authoring for the dashboard People & Roles editor: create/edit and
soft-remove a person + role (+ optional embedded bio). Pure graph/KB writes via the shared
helpers; the caller owns the transaction (commit) and any embed trigger. source='dashboard',
so the crawler never touches these."""
from __future__ import annotations

import json
import re
import sqlite3

from v2.core.graph.orgs import ensure_org, org_node_id, sync_org_nodes
from v2.core.graph.project import project_appointment


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _org_slug(conn: sqlite3.Connection, org_id: int) -> str:
    row = conn.execute("SELECT slug FROM organizations WHERE id=?", (org_id,)).fetchone()
    if not row:
        raise ValueError(f"no organization id={org_id}")
    return row[0]


def add_or_edit_person(conn: sqlite3.Connection, *, org_id: int, name: str, title: str,
                       category: str, email: str | None = None,
                       about: str | None = None, source: str = "dashboard") -> dict:
    """Upsert a Person + one has_role edge (free-text title, category) under org_id, merge
    email into the node attrs, and (re)write an optional bio knowledge_item. Idempotent on
    the person key. Returns {person_key, bio_item_id|None}. Does NOT commit."""
    org_slug = _org_slug(conn, org_id)
    key = f"{source}/{org_slug}/{_slug(name)}"
    sync_org_nodes(conn)
    pid = project_appointment(conn, person_key=key, name=name, org_id=org_id,
                              category=category, titles=[title],
                              source_section="manual", source=source)
    if email:
        row = conn.execute("SELECT attrs FROM nodes WHERE id=?", (pid,)).fetchone()
        attrs = json.loads(row[0]) if row and row[0] else {}
        attrs["email"] = email
        conn.execute("UPDATE nodes SET attrs=?, updated_at=datetime('now') WHERE id=?",
                     (json.dumps(attrs), pid))
    bio_id = None
    conn.execute("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
                 "WHERE is_active=1 AND json_extract(metadata,'$.entity_id')=? "
                 "AND created_by=?", (key, source))
    if about and about.strip():
        meta = json.dumps({"entity_id": key, "verified": True,
                           "natural_key": f"{key}:profile:main"})
        content = f"{name} — {title}. {about.strip()}"
        cur = conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,metadata,version,"
            "is_active,created_by) VALUES(?,?,?,?,?,1,1,?)",
            (org_id, "profile", name, content, meta, source))
        conn.execute("UPDATE knowledge_items SET root_id=? WHERE id=?",
                     (cur.lastrowid, cur.lastrowid))
        bio_id = cur.lastrowid
    return {"person_key": key, "bio_item_id": bio_id}


def remove_person_role(conn: sqlite3.Connection, *, person_key: str, org_id: int,
                       source: str = "dashboard") -> dict:
    """Soft-remove: deactivate this person's has_role edge to org_id; if they then have no
    other active role, deactivate the Person node; retire their bio. Returns
    {removed, person_deactivated}. Does NOT commit."""
    prow = conn.execute("SELECT id FROM nodes WHERE type='Person' AND key=?",
                        (person_key,)).fetchone()
    if not prow:
        return {"removed": False, "person_deactivated": False}
    pid = prow[0]
    onode = org_node_id(conn, org_id)
    edges = conn.execute(
        "SELECT id FROM edges WHERE src_id=? AND dst_id=? AND type='has_role' AND is_active=1",
        (pid, onode)).fetchall()
    for (eid,) in edges:
        conn.execute("UPDATE edges SET is_active=0, updated_at=datetime('now') WHERE id=?", (eid,))
    removed = bool(edges)
    remaining = conn.execute(
        "SELECT COUNT(*) FROM edges WHERE src_id=? AND type='has_role' AND is_active=1",
        (pid,)).fetchone()[0]
    person_deactivated = False
    if remaining == 0:
        conn.execute("UPDATE nodes SET is_active=0, updated_at=datetime('now') WHERE id=?", (pid,))
        person_deactivated = True
    conn.execute("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
                 "WHERE is_active=1 AND json_extract(metadata,'$.entity_id')=? AND created_by=?",
                 (person_key, source))
    return {"removed": removed, "person_deactivated": person_deactivated}
