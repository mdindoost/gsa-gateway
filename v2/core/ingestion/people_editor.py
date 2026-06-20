"""Single-person manual authoring for the dashboard People & Roles editor: create/edit and
soft-remove a person + role (+ optional embedded bio). Pure graph/KB writes via the shared
helpers; the caller owns the transaction (commit) and any embed trigger. source='dashboard',
so the crawler never touches these."""
from __future__ import annotations

import json
import re
import sqlite3

from v2.core.graph.orgs import ensure_org, org_node_id, sync_org_nodes
from v2.core.graph.project import project_appointment, area_key
from v2.core.graph.store import active_edge_ids_from, deactivate_edges, upsert_edge, upsert_node


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _org_slug(conn: sqlite3.Connection, org_id: int) -> str:
    row = conn.execute("SELECT slug FROM organizations WHERE id=?", (org_id,)).fetchone()
    if not row:
        raise ValueError(f"no organization id={org_id}")
    return row[0]


def _coerce_profile_value(field_key: str, k: str, v):
    """Keep metrics as JSON numbers (so a future 'most-cited' skill can ORDER BY them).
    URLs and timestamps stay strings; an int-like string elsewhere becomes an int."""
    if k in ("url", "updated_at") or not isinstance(v, str):
        return v
    s = v.strip().replace(",", "")
    if s.lstrip("-").isdigit():
        return int(s)
    return v


def set_person_profiles(conn: sqlite3.Connection, *, person_key: str,
                        profiles: dict, replace: bool = False) -> dict:
    """Merge external-profile data into a Person node's ``attrs.profiles``. Each key in
    ``profiles`` (scholar/linkedin/orcid/website/…) maps to a dict like
    ``{"url":…, "citations":…}``. Deep-merges per field by default (setting metrics keeps
    the url); ``replace=True`` overwrites a field's dict wholesale; a field mapped to None
    removes it. Metric strings are coerced to numbers. Does NOT commit (caller owns the txn).

    Storage is a generic bag — any field key is accepted; the registry
    (v2/core/people/profile_fields.py) governs *display*, not storage."""
    row = conn.execute(
        "SELECT id, attrs FROM nodes WHERE type='Person' AND key=? AND is_active=1",
        (person_key,)).fetchone()
    if not row:
        raise ValueError(f"no active Person with key {person_key!r}")
    pid, raw = row
    attrs = json.loads(raw) if raw else {}
    bag = attrs.get("profiles") or {}
    for fkey, data in (profiles or {}).items():
        if data is None:
            bag.pop(fkey, None)
            continue
        clean = {k: _coerce_profile_value(fkey, k, v) for k, v in dict(data).items()}
        if replace or not isinstance(bag.get(fkey), dict):
            bag[fkey] = clean
        else:
            bag[fkey].update(clean)
    attrs["profiles"] = bag
    conn.execute("UPDATE nodes SET attrs=?, updated_at=datetime('now') WHERE id=?",
                 (json.dumps(attrs), pid))
    return {"person_key": person_key, "profiles": bag}


def set_person_research_areas(conn: sqlite3.Connection, *, person_key: str, areas: list[str],
                             org_id: int, source: str = "scholar") -> dict:
    """Merge externally-sourced research areas (e.g. Google Scholar interests) into the KG as the
    SAME artifacts the crawler produces — ResearchArea nodes + `researches` edges + ONE
    `research_areas` knowledge_item — all tagged ``source`` so crawler data is never touched.
    Union/dedup by ``area_key`` (reuses the existing area node when present). Idempotent:
    deactivate-then-insert for the KB item; source-scoped reconcile for the edges. ``org_id`` should
    be the person's faculty home org (so org-scoped 'who works on X' finds them). Does NOT commit."""
    row = conn.execute(
        "SELECT id, name FROM nodes WHERE type='Person' AND key=? AND is_active=1",
        (person_key,)).fetchone()
    if not row:
        raise ValueError(f"no active Person with key {person_key!r}")
    pid, name = row
    clean, seen = [], set()
    for a in areas or []:
        a = (a or "").strip()
        k = area_key(a)
        if a and k not in seen:
            seen.add(k)
            clean.append(a)
    # graph: ResearchArea nodes + researches edges, source-scoped reconcile (crawler edges untouched)
    keep: set[int] = set()
    for a in clean:
        # Reuse an existing ResearchArea node WITHOUT renaming it — a self-asserted scholar tag
        # must not downgrade the crawler's curated display casing (e.g. "Machine Learning").
        existing = conn.execute(
            "SELECT id FROM nodes WHERE type='ResearchArea' AND key=?", (area_key(a),)).fetchone()
        if existing:
            anode = existing[0]
            conn.execute("UPDATE nodes SET is_active=1, updated_at=datetime('now') WHERE id=?",
                         (anode,))
        else:
            anode = upsert_node(conn, type="ResearchArea", key=area_key(a), name=a, source=source)
        keep.add(upsert_edge(conn, src_id=pid, type="researches", dst_id=anode,
                             area_source="external", source=source))
    deactivate_edges(
        conn, active_edge_ids_from(conn, pid, type="researches", source=source) - keep)
    # KB item: deactivate-then-insert (the add_or_edit_person pattern), distinct natural_key,
    # created_by=source so the crawler's own research_areas item (created_by='crawler') is untouched.
    conn.execute(
        "UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') WHERE is_active=1 "
        "AND type='research_areas' AND created_by=? AND json_extract(metadata,'$.entity_id')=?",
        (source, person_key))
    item_id = None
    if clean:
        meta = json.dumps({"entity_id": person_key, "verified": True, "area_source": source,
                           "areas": clean, "natural_key": f"{person_key}:research_areas:{source}"})
        content = f"Research areas of {name}: " + "; ".join(clean)
        item_id = conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,metadata,version,is_active,"
            "created_by) VALUES(?,?,?,?,?,1,1,?)",
            (org_id, "research_areas", name, content, meta, source)).lastrowid
    return {"person_key": person_key, "areas": clean, "item_id": item_id}


def add_or_edit_person(conn: sqlite3.Connection, *, org_id: int, name: str, title: str,
                       category: str, email: str | None = None,
                       about: str | None = None, source: str = "dashboard",
                       profiles: dict | None = None) -> dict:
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
    if profiles:
        set_person_profiles(conn, person_key=key, profiles=profiles)
    bio_id = None
    conn.execute("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
                 "WHERE is_active=1 AND json_extract(metadata,'$.entity_id')=? "
                 "AND created_by=?", (key, source))
    if about and about.strip():
        meta = json.dumps({"entity_id": key, "verified": True,
                           "natural_key": f"{key}:profile:main", "about": about.strip()})
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
