"""Project a GSA officer/RGO roster into the graph (manual people path). Pure: takes a
dict in, writes nodes/edges via the shared graph helpers, source='dashboard'. The crawl
adapter (Plan 2) would feed the SAME shapes, so this is the single projection path."""
from __future__ import annotations

import json
import re
import sqlite3

from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _project_people(conn, org_id, org_slug, people) -> list[tuple[int, str]]:
    touched: list[tuple[int, str]] = []
    for p in people:
        key = f"dashboard/{org_slug}/{_slug(p['name'])}"
        pid = project_appointment(
            conn, person_key=key, name=p["name"], org_id=org_id,
            category=p.get("category", "officer"), titles=[p["title"]],
            source_section=p.get("source_section", "roster"), source="dashboard")
        # carry email/note as node attrs (additive; project_appointment passes attrs=None)
        extra = {k: p[k] for k in ("email", "note") if p.get(k)}
        if extra:
            row = conn.execute("SELECT attrs FROM nodes WHERE id=?", (pid,)).fetchone()
            attrs = json.loads(row[0]) if row and row[0] else {}
            attrs.update(extra)
            conn.execute("UPDATE nodes SET attrs=?, updated_at=datetime('now') WHERE id=?",
                         (json.dumps(attrs), pid))
        touched.append((org_id, key))
    return touched


def project_roster(conn: sqlite3.Connection, roster: dict) -> list[tuple[int, str]]:
    """Create the GSA org (if needed), its officers, and each RGO + its officers. Returns
    the list of (org_id, person_key) appointments touched — feed it to reconcile_roster."""
    o = roster["org"]
    gsa_id = ensure_org(conn, o["slug"], o["name"], parent_slug=o.get("parent"), type="custom")
    touched = _project_people(conn, gsa_id, o["slug"], roster.get("people", []))
    for rgo in roster.get("rgos", []):
        rid = ensure_org(conn, rgo["slug"], rgo["name"], parent_slug=o["slug"], type="unit")
        touched += _project_people(conn, rid, rgo["slug"], rgo.get("people", []))
    sync_org_nodes(conn)
    return touched


def reconcile_roster(conn: sqlite3.Connection, present: list[tuple[int, str]]) -> int:
    """Deactivate dashboard officer/deprep appointments that are no longer in the roster —
    scoped to the orgs the roster touched, so unrelated people are never affected. Returns
    the number of appointments retired. Mirrors the crawler's section-scoped M3 sweep."""
    present_set = set(present)
    org_ids = {oid for oid, _ in present}
    retired = 0
    for org_id in org_ids:
        rows = conn.execute(
            "SELECT e.id, p.key FROM edges e "
            "JOIN nodes p ON p.id=e.src_id JOIN nodes o ON o.id=e.dst_id "
            "WHERE e.type='has_role' AND e.is_active=1 AND e.source='dashboard' "
            "AND e.category IN ('officer','deprep') "
            "AND json_extract(o.attrs,'$.org_id')=?", (org_id,)).fetchall()
        for eid, pkey in rows:
            if (org_id, pkey) not in present_set:
                conn.execute("UPDATE edges SET is_active=0, updated_at=datetime('now') "
                             "WHERE id=?", (eid,))
                retired += 1
    return retired
