"""Project one EntityRecord into the graph: a Person node, its home has_role edge,
and structured `researches` edges. Deterministic only (the LLM is Phase 2). Runs
inside the reconcile transaction so it is atomic with the text rows (B1)."""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter

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
    """Case-folded grouping key for a ResearchArea node."""
    return area.strip().casefold()


def canonical_area(forms: list[str]) -> str:
    """Pick the display casing for a case-folded group: most frequent surface form, ties
    broken case-insensitively first (then raw) so the choice is deterministic and not an
    artifact of ASCII case ordering (cosmetic only — never a wrong fact). Shared by the
    area facets (skills) and the research_of_person union (entity)."""
    counts = Counter(forms)
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].casefold(), kv[0]))[0][0]


def project_entity(conn: sqlite3.Connection, rec: EntityRecord, org_id: int,
                   source: str = "crawler", home_appointment: bool = True) -> int:
    """Enrich a person from their profile: attrs + structured research edges, and
    (when ``home_appointment``) their home has_role from the profile title.

    In the explore() flow listings are the AUTHORITATIVE source of appointments (the
    section gives the role category), and they always run before profiles — so the
    profile pass passes ``home_appointment=False`` and only adds attrs+research, never
    creating or clobbering a role (e.g. it must not turn a 'Staff'-section person into
    'admin' because their title carries an '…Office of the Dean' suffix). The standalone
    single-profile ingest path (no listing) keeps the default True. Returns the Person id."""
    # MERGE into existing attrs (don't rebuild) so a re-crawl never clobbers the
    # external-profile bag (manually-set Scholar metrics etc.). upsert_node overwrites
    # the whole attrs blob, so we must hand it the merged dict.
    erow = conn.execute("SELECT attrs FROM nodes WHERE type='Person' AND key=?",
                        (rec.entity_id,)).fetchone()
    attrs = json.loads(erow[0]) if erow and erow[0] else {}
    for k, v in {
        "email": rec.contact.get("email"),
        "phone": rec.contact.get("phone"),
        "office": rec.contact.get("office"),
        "website": rec.links.get("website"),
    }.items():
        if v:
            attrs[k] = v
    # Auto-capture profile links the crawler found on the page (scholar/linkedin/orcid)
    # into attrs.profiles — per-field merge KEEPS any existing metrics on that field.
    profiles = dict(attrs.get("profiles") or {})
    for fkey in ("scholar", "linkedin", "orcid", "github"):
        url = rec.links.get(fkey)
        if url:
            entry = dict(profiles.get(fkey) or {})
            entry["url"] = url
            profiles[fkey] = entry
    if profiles:
        attrs["profiles"] = profiles
    pid = upsert_node(conn, type="Person", key=rec.entity_id, name=rec.name,
                      attrs=attrs, source=source)

    if home_appointment:
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
                        source_section: str, source: str = "crawler",
                        merge: bool = False) -> int:
    """Record ONE appointment from a listing appearance — additively (multi-membership).

    Upserts the Person (preserving any attrs a profile pass already set — attrs=None) and a
    single ``has_role`` edge for (person, org) with the SECTION-derived ``category``. It does
    NOT touch the person's appointments in OTHER orgs or their research edges, so a person
    reached from two paths (e.g. Wang via College Administration *and* CS) accumulates both
    roles instead of one wiping the other. Returns the Person node id."""
    pid = upsert_node(conn, type="Person", key=person_key, name=name, attrs=None, source=source)
    onode = org_node_id(conn, org_id)
    if merge:
        # A second page of the SAME crawl re-appointing this person to this org: UNION the new
        # titles into the existing edge and PRESERVE its category/source_section/other attrs
        # (e.g. is_primary). Never let an admin page flip a faculty edge's category, and never
        # drop attrs a profile pass set. (Caller gates `merge` so the first touch of a run still
        # overwrites — so a changed title isn't kept stale.)
        row = conn.execute(
            "SELECT attrs, category, source_section FROM edges "
            "WHERE src_id=? AND type='has_role' AND dst_id=?", (pid, onode)).fetchone()
        if row:
            old = json.loads(row[0]) if row[0] else {}
            old["titles"] = list(dict.fromkeys((old.get("titles") or []) + (titles or [])))
            upsert_edge(conn, src_id=pid, type="has_role", dst_id=onode,
                        category=row[1] if row[1] is not None else category,
                        source_section=row[2] or source_section, attrs=old, source=source)
            return pid
    upsert_edge(conn, src_id=pid, type="has_role", dst_id=onode,
                category=category, source_section=source_section,
                attrs={"titles": titles}, source=source)
    return pid
