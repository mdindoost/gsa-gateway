"""Node/edge CRUD for the knowledge graph. All writes assume the caller manages
the transaction (the reconcile step runs these inside its own `with conn:`)."""
from __future__ import annotations

import json
import sqlite3


def upsert_node(conn: sqlite3.Connection, *, type: str, key: str, name: str,
                attrs: dict | None = None, source: str = "crawler",
                source_doc_id: int | None = None, ontology_version: int = 1) -> int:
    """Insert or update a node by its (type, key) identity; returns the node id and
    (re)activates it. ``attrs=None`` on an EXISTING node leaves its attrs untouched — so a
    coarse listing pass can create/refresh a Person without clobbering richer attrs a later
    profile pass set (additive enrichment). On insert, None means empty ``{}``."""
    row = conn.execute("SELECT id FROM nodes WHERE type=? AND key=?", (type, key)).fetchone()
    if row:
        nid = row[0]
        if attrs is not None:
            conn.execute(
                "UPDATE nodes SET name=?, attrs=?, source=?, source_doc_id=?, "
                "ontology_version=?, is_active=1, updated_at=datetime('now') WHERE id=?",
                (name, json.dumps(attrs), source, source_doc_id, ontology_version, nid))
        else:
            conn.execute(
                "UPDATE nodes SET name=?, source=?, source_doc_id=?, "
                "ontology_version=?, is_active=1, updated_at=datetime('now') WHERE id=?",
                (name, source, source_doc_id, ontology_version, nid))
        return nid
    a = json.dumps(attrs or {})
    cur = conn.execute(
        "INSERT INTO nodes(type,key,name,attrs,source,source_doc_id,ontology_version) "
        "VALUES(?,?,?,?,?,?,?)", (type, key, name, a, source, source_doc_id, ontology_version))
    return cur.lastrowid


def upsert_edge(conn: sqlite3.Connection, *, src_id: int, type: str, dst_id: int,
                category: str | None = None, area_source: str | None = None,
                source_section: str | None = None, attrs: dict | None = None,
                source: str = "crawler", source_doc_id: int | None = None,
                ontology_version: int = 1) -> int:
    """Insert or update an edge by its (src_id, type, dst_id) identity; returns the id."""
    a = json.dumps(attrs or {})
    row = conn.execute("SELECT id FROM edges WHERE src_id=? AND type=? AND dst_id=?",
                       (src_id, type, dst_id)).fetchone()
    if row:
        eid = row[0]
        conn.execute(
            "UPDATE edges SET category=?, area_source=?, source_section=?, attrs=?, "
            "source=?, source_doc_id=?, ontology_version=?, is_active=1, "
            "updated_at=datetime('now') WHERE id=?",
            (category, area_source, source_section, a, source, source_doc_id,
             ontology_version, eid))
        return eid
    cur = conn.execute(
        "INSERT INTO edges(src_id,type,dst_id,category,area_source,source_section,"
        "attrs,source,source_doc_id,ontology_version) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (src_id, type, dst_id, category, area_source, source_section, a, source,
         source_doc_id, ontology_version))
    return cur.lastrowid


def active_edge_ids_from(conn: sqlite3.Connection, src_id: int,
                         type: str | None = None, source: str = "crawler") -> set[int]:
    """Active edge ids leaving ``src_id`` (optionally one type), scoped to a source."""
    q = "SELECT id FROM edges WHERE src_id=? AND is_active=1 AND source=?"
    p: list = [src_id, source]
    if type:
        q += " AND type=?"
        p.append(type)
    return {r[0] for r in conn.execute(q, p)}


def deactivate_edges(conn: sqlite3.Connection, edge_ids) -> None:
    conn.executemany(
        "UPDATE edges SET is_active=0, updated_at=datetime('now') WHERE id=?",
        [(e,) for e in edge_ids])
