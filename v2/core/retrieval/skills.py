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
from collections import Counter

# Hand aliases beyond what organizations.name/slug already cover.
_ORG_ALIASES = {
    "cs": "computer-science", "comp sci": "computer-science",
    "ds": "data-science",
    "ywcc": "ywcc", "ying wu college": "ywcc",
    "ying wu college of computing": "ywcc",
}
_RESEARCH_TYPES = ("research_areas", "research_statement", "overview")

# Curated, org-agnostic vocabulary map (Phase 2, spec docs/superpowers/specs/
# 2026-06-14-semantic-area-matching.md). A query abbreviation/synonym expands into the
# words faculty profiles actually use, so token-exact FTS bridges "llm" → "large language
# models". This is controlled-vocabulary query expansion (cf. MeSH/PubMed), not a
# per-question patch: ONE mechanism, every phrase justified by real KB FTS counts, and an
# unmapped term degrades to Phase-1 exact match. Intentionally kept TIGHT — "llm" does NOT
# expand to "machine learning"/"ai" (would over-match); only LLM-specific phrasings.
AREA_SYNONYMS: dict[str, list[str]] = {
    "llm":  ["llm", "large language model", "large language models", "generative ai"],
    "llms": ["llm", "large language model", "large language models", "generative ai"],
    "nlp":  ["nlp", "natural language processing", "natural language"],
    "ai":   ["ai", "artificial intelligence"],
    "ml":   ["ml", "machine learning"],
    "cv":   ["cv", "computer vision"],
    "hci":  ["hci", "human computer interaction", "human-computer interaction"],
}


def _normalize_area(area: str) -> str:
    """Lowercase, strip, collapse internal whitespace — the map's lookup key."""
    return " ".join((area or "").lower().split())


def expand_area(area: str) -> list[str]:
    """Expand an area term into the curated set of FTS phrases to match (the term itself
    plus known synonyms). Unmapped terms return ``[term]`` — identical to Phase-1 exact
    match, so expansion only ever ADDS recall for known abbreviations, never regresses."""
    key = _normalize_area(area)
    return list(AREA_SYNONYMS.get(key, [key]))


def _fts_term(area: str) -> str:
    """Quote the area as an FTS5 phrase so multi-word terms match and operators
    (- * : " OR NEAR) can't break the query."""
    return '"' + (area or "").strip().replace('"', '""') + '"'


def _fts_query(area: str) -> str:
    """Build the FTS5 MATCH expression for an area: an OR of its expanded phrases, each a
    quoted phrase (word-boundary, operator-safe)."""
    return " OR ".join(_fts_term(p) for p in expand_area(area))


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
    params: list = [_fts_query(area), *_RESEARCH_TYPES]
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


def _area_rows(conn: sqlite3.Connection, org_id: int | None) -> list[tuple[str, str]]:
    """(area_value, entity_id) for every tag on active research_areas items, optionally
    scoped to an org subtree. Reads metadata.areas via json_each."""
    clause, params = "", []
    if org_id is not None:
        ids = sorted(org_descendants(conn, org_id))
        clause = " AND k.org_id IN (%s)" % ",".join("?" * len(ids))
        params = list(ids)
    q = ("SELECT je.value, json_extract(k.metadata,'$.entity_id') "
         "FROM knowledge_items k, json_each(k.metadata,'$.areas') je "
         "WHERE k.type='research_areas' AND k.is_active=1 "
         "AND json_extract(k.metadata,'$.entity_id') IS NOT NULL" + clause)
    out: list[tuple[str, str]] = []
    for val, eid in conn.execute(q, params):
        if val and val.strip() and eid:
            out.append((val.strip(), eid))
    return out


def _canonical(forms: list[str]) -> str:
    """Pick the display casing for a case-folded group: most frequent surface form,
    ties broken alphabetically (cosmetic only — never a wrong fact)."""
    counts = Counter(forms)
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def areas_in_org(conn: sqlite3.Connection, org_id: int) -> list[str]:
    """Distinct research areas across an org subtree, case-folded for grouping and shown
    in a canonical casing. The new enumerable facet ('what areas does CS cover?')."""
    groups: dict[str, list[str]] = {}
    for val, _eid in _area_rows(conn, org_id):
        groups.setdefault(val.casefold(), []).append(val)
    return sorted((_canonical(forms) for forms in groups.values()), key=str.casefold)


def area_counts(conn: sqlite3.Connection, org_id: int) -> list[tuple[str, int]]:
    """(canonical_area, distinct_faculty_count) across an org subtree, most faculty first."""
    forms: dict[str, list[str]] = {}
    ents: dict[str, set[str]] = {}
    for val, eid in _area_rows(conn, org_id):
        k = val.casefold()
        forms.setdefault(k, []).append(val)
        ents.setdefault(k, set()).add(eid)
    out = [(_canonical(forms[k]), len(ents[k])) for k in forms]
    return sorted(out, key=lambda t: (-t[1], t[0].casefold()))


def people_by_area_tag(conn: sqlite3.Connection, area: str,
                       org_id: int | None = None) -> list[tuple[str, str]]:
    """Faculty (name, entity_id) who LIST ``area`` as a research-area tag — exact
    (case-folded) match against metadata.areas, with P2 expansion so 'ml'/'llm' hit the
    canonical tags. Precise, lower-recall (only faculty who list discrete areas)."""
    targets = {p.casefold() for p in expand_area(area)}
    eids = {eid for val, eid in _area_rows(conn, org_id) if val.casefold() in targets}
    return sorted((_display_name(conn, e), e) for e in eids)
