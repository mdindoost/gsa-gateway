"""Structured-retrieval skills — parameterized query templates over the live KB.

Phase 1 of the retrieval redesign (docs/superpowers/specs/2026-06-13-structured-
retrieval-phase1.md). These answer the question shapes semantic top-K RAG fails on —
enumerate / filter / traverse / count — with **complete, deterministic** SQL results.

Design rules (from the senior review, verified against the DB):
- Research-area matching uses FTS5 ``MATCH`` (word-boundary), NEVER substring LIKE —
  substring 'graph' wrongly matches graphics/cryptography/geographic.
- Every entity query filters ``is_active=1`` — knowledge_fts indexes inactive versions
  too, which would inflate counts/rosters.
- "in <org>" includes the org itself plus all descendants.
- A "person" = a distinct ``metadata.entity_id``; the display name is the profile title.

All functions take a sqlite3 connection (caller opens it; see the integration layer).
"""

from __future__ import annotations

import json
import sqlite3

# Hand aliases beyond what organizations.name/slug already cover.
_ORG_ALIASES = {
    "cs": "computer-science", "comp sci": "computer-science",
    "ds": "data-science",
    "ywcc": "ywcc", "ying wu college": "ywcc",
    "ying wu college of computing": "ywcc",
}
_RESEARCH_TYPES = ("research_areas", "research_statement", "overview")


def _fts_term(area: str) -> str:
    """Quote the area as an FTS5 phrase so multi-word terms match and operators
    (- * : " OR NEAR) can't break the query."""
    return '"' + (area or "").strip().replace('"', '""') + '"'


def resolve_org(conn: sqlite3.Connection, name: str) -> int | None:
    """Map an org name/slug/alias to an org id (case-insensitive), or None."""
    if not name:
        return None
    key = name.strip().lower()
    row = conn.execute(
        "SELECT id FROM organizations WHERE is_active=1 AND (lower(name)=? OR lower(slug)=?)",
        (key, key)).fetchone()
    if row:
        return row[0]
    slug = _ORG_ALIASES.get(key)
    if slug:
        row = conn.execute(
            "SELECT id FROM organizations WHERE is_active=1 AND lower(slug)=?", (slug,)).fetchone()
        if row:
            return row[0]
    return None


def org_descendants(conn: sqlite3.Connection, org_id: int) -> set[int]:
    """The org itself plus every active descendant (so 'in YWCC' catches sub-depts
    and anyone attached directly to the college node)."""
    out = {org_id}
    frontier = [org_id]
    while frontier:
        nxt: list[int] = []
        for pid in frontier:
            for (cid,) in conn.execute(
                    "SELECT id FROM organizations WHERE parent_id=? AND is_active=1", (pid,)):
                if cid not in out:
                    out.add(cid)
                    nxt.append(cid)
        frontier = nxt
    return out


def org_departments(conn: sqlite3.Connection, org_id: int) -> list[str]:
    """Immediate child org names (e.g. YWCC → Computer Science, Data Science, …)."""
    return [r[0] for r in conn.execute(
        "SELECT name FROM organizations WHERE parent_id=? AND is_active=1 ORDER BY name",
        (org_id,))]


def _display_name(conn: sqlite3.Connection, entity_id: str) -> str:
    for typ in ("profile", "overview"):
        r = conn.execute(
            "SELECT title FROM knowledge_items WHERE type=? AND is_active=1 "
            "AND json_extract(metadata,'$.entity_id')=? AND title IS NOT NULL LIMIT 1",
            (typ, entity_id)).fetchone()
        if r and r[0]:
            return r[0].split("—")[0].strip() if typ == "overview" else r[0]
    return entity_id.rsplit("/", 1)[-1]


def faculty_in_department(conn: sqlite3.Connection, org_id: int) -> list[tuple[str, str]]:
    """All faculty (name, entity_id) filed under a department, sorted by name."""
    rows = conn.execute(
        "SELECT DISTINCT json_extract(metadata,'$.entity_id') FROM knowledge_items "
        "WHERE is_active=1 AND org_id=? AND json_extract(metadata,'$.entity_id') IS NOT NULL",
        (org_id,)).fetchall()
    return sorted((_display_name(conn, e), e) for (e,) in rows)


def _research_entities(conn: sqlite3.Connection, area: str, org_id: int | None) -> set[str]:
    params: list = [_fts_term(area), *_RESEARCH_TYPES]
    org_clause = ""
    if org_id is not None:
        ids = sorted(org_descendants(conn, org_id))
        org_clause = " AND k.org_id IN (%s)" % ",".join("?" * len(ids))
        params += ids
    q = (
        "SELECT DISTINCT json_extract(k.metadata,'$.entity_id') "
        "FROM knowledge_fts f JOIN knowledge_items k ON k.id=f.rowid "
        "WHERE f.search_text MATCH ? AND k.is_active=1 "
        f"AND k.type IN ({','.join('?' * len(_RESEARCH_TYPES))})" + org_clause +
        " AND json_extract(k.metadata,'$.entity_id') IS NOT NULL")
    return {r[0] for r in conn.execute(q, params) if r[0]}


def people_by_research_area(conn: sqlite3.Connection, area: str,
                            org_id: int | None = None) -> list[tuple[str, str]]:
    """All faculty (name, entity_id) whose research matches ``area`` (FTS word-boundary),
    optionally scoped to an org subtree. Complete and stable — no top-K."""
    return sorted((_display_name(conn, e), e) for e in _research_entities(conn, area, org_id))


def count_people_by_research_area(conn: sqlite3.Connection, area: str,
                                  org_id: int | None = None) -> int:
    """Count of distinct faculty matching ``area`` — same query as the list, so they
    can never disagree."""
    return len(_research_entities(conn, area, org_id))
