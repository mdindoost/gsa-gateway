"""Project one EntityRecord into the graph: a Person node, its home has_role edge,
and structured `researches` edges. Deterministic only (the LLM is Phase 2). Runs
inside the reconcile transaction so it is atomic with the text rows (B1)."""
from __future__ import annotations

import re
import sqlite3

from v2.core.graph.orgs import org_node_id
from v2.core.graph.store import (
    active_edge_ids_from, deactivate_edges, upsert_edge, upsert_node)
from v2.core.ingestion.entity import EntityRecord

# Order matters: a faculty title wins even when an admin title is also present
# (e.g. "Professor" + "Associate Dean" -> faculty home appointment). 1b refines
# this with the listing section.
_CATEGORY_RULES = [
    (re.compile(r"\b(professor|lecturer)\b", re.I), "faculty"),
    (re.compile(r"\bemerit", re.I), "emeritus"),
    (re.compile(r"\bdean\b", re.I), "admin"),
    (re.compile(r"\badvis", re.I), "advisor"),
    (re.compile(r"\b(director|coordinator|designer|administrat|manager|assistant to)\b", re.I), "staff"),
]


def category_from_titles(titles: list[str]) -> str:
    hay = " ; ".join(titles)
    for rx, cat in _CATEGORY_RULES:
        if rx.search(hay):
            return cat
    return "staff"


def area_key(area: str) -> str:
    """Case-folded grouping key for a ResearchArea node (display canonicalization for
    the facets is Phase 3, reusing skills._canonical)."""
    return area.strip().casefold()


def project_entity(conn: sqlite3.Connection, rec: EntityRecord, org_id: int,
                   source: str = "crawler") -> int:
    """Rebuild this entity's graph to match ``rec``; deactivate its crawler edges that
    are no longer present. Returns the Person node id."""
    attrs = {k: v for k, v in {
        "email": rec.contact.get("email"),
        "phone": rec.contact.get("phone"),
        "office": rec.contact.get("office"),
        "website": rec.links.get("website"),
    }.items() if v}
    pid = upsert_node(conn, type="Person", key=rec.entity_id, name=rec.name,
                      attrs=attrs, source=source)

    # the home appointment (from the profile's own title); upserted, never swept here
    upsert_edge(
        conn, src_id=pid, type="has_role", dst_id=org_node_id(conn, org_id),
        category=category_from_titles(rec.titles),
        attrs={"titles": rec.titles, "is_primary": True}, source=source)

    keep_research: set[int] = set()
    seen: set[str] = set()
    for area in rec.research_areas:
        a = area.strip()
        if not a:
            continue
        k = area_key(a)
        if k in seen:
            continue
        seen.add(k)
        anode = upsert_node(conn, type="ResearchArea", key=k, name=a, source=source)
        keep_research.add(upsert_edge(conn, src_id=pid, type="researches", dst_id=anode,
                                      area_source="structured", source=source))

    # Scope deactivation to THIS profile's research edges only — never sweep the person's
    # appointments in OTHER orgs (multi-membership created from listings).
    deactivate_edges(
        conn, active_edge_ids_from(conn, pid, type="researches", source=source) - keep_research)
    return pid


def project_appointment(conn: sqlite3.Connection, *, person_key: str, name: str,
                        org_id: int, category: str | None, titles: list[str],
                        source_section: str, source: str = "crawler") -> int:
    """Record ONE appointment from a listing appearance — additively (multi-membership).

    Upserts the Person (preserving any attrs a profile pass already set — attrs=None) and a
    single ``has_role`` edge for (person, org) with the SECTION-derived ``category``. It does
    NOT touch the person's appointments in OTHER orgs or their research edges, so a person
    reached from two paths (e.g. Wang via College Administration *and* CS) accumulates both
    roles instead of one wiping the other. Returns the Person node id."""
    pid = upsert_node(conn, type="Person", key=person_key, name=name, attrs=None, source=source)
    upsert_edge(conn, src_id=pid, type="has_role", dst_id=org_node_id(conn, org_id),
                category=category, source_section=source_section,
                attrs={"titles": titles}, source=source)
    return pid
