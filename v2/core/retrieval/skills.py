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
import re
import sqlite3
from collections import Counter

from v2.core.retrieval.entity import normalize_person_name, research_of_person

# Hand aliases beyond what organizations.name/slug already cover.
_ORG_ALIASES = {
    "cs": "computer-science", "comp sci": "computer-science",
    "ds": "data-science",
    "ywcc": "ywcc", "ying wu college": "ywcc",
    "ying wu college of computing": "ywcc",
}
_RESEARCH_TYPES = ("research_areas", "research_statement", "overview")

# Shared enum: the ONLY org types WS3 enumerates. Imported by router.py + slot_extractor.py so the
# guard lives in one place (review MAJOR: validate org_type in the skill, not only at call sites).
ORG_TYPE_ENUM: tuple[str, ...] = ("club", "department", "college")

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


def orgs_by_type(conn: sqlite3.Connection, org_type: str,
                 parent_org_id: int | None = None) -> list[str]:
    """Active org names of a given ``type`` (e.g. 'club', 'college', 'department'), optionally scoped to
    a parent (``parent_id``). The single type-filtered enumeration; org_departments delegates here so the
    child-enumeration SQL lives in ONE place (WS3 coexist + DRY). Unknown type ⇒ [] (never raises)."""
    if org_type not in ORG_TYPE_ENUM:
        return []
    if parent_org_id is None:
        rows = conn.execute(
            "SELECT name FROM organizations WHERE type=? AND is_active=1 ORDER BY name",
            (org_type,))
    else:
        rows = conn.execute(
            "SELECT name FROM organizations WHERE type=? AND is_active=1 AND parent_id=? "
            "ORDER BY name", (org_type, parent_org_id))
    return [r[0] for r in rows]


def org_departments(conn: sqlite3.Connection, org_id: int) -> list[str]:
    """Immediate child org names that are actual departments (e.g. YWCC → Computer Science,
    Data Science, …). Delegates to orgs_by_type(type='department', parent=org_id) — one SQL path."""
    return orgs_by_type(conn, "department", org_id)


def _display_names(conn: sqlite3.Connection,
                   entity_ids: list[str]) -> dict[str, str]:
    """Resolve many entity_ids → display names in ONE query (avoids the per-entity N+1).
    Prefers a 'profile' title; falls back to an 'overview' title (stripped at '—'); then
    to the entity_id tail. Order-stable on the input ids."""
    ids = list(dict.fromkeys(e for e in entity_ids if e))   # dedup, preserve order
    resolved: dict[str, str] = {}
    if ids:
        ph = ",".join("?" * len(ids))
        # profile rows first, so the first row seen per entity is the preferred one.
        q = ("SELECT json_extract(metadata,'$.entity_id'), type, title "
             "FROM knowledge_items WHERE is_active=1 AND title IS NOT NULL "
             "AND type IN ('profile','overview') "
             f"AND json_extract(metadata,'$.entity_id') IN ({ph}) "
             "ORDER BY CASE type WHEN 'profile' THEN 0 ELSE 1 END")
        for eid, typ, title in conn.execute(q, ids):
            if eid in resolved:
                continue
            resolved[eid] = title if typ == "profile" else title.split("—")[0].strip()
        # Fall back to the Person node's name for ids with no KB title (manually-seeded people,
        # e.g. Theatre, have a graph node but no profile knowledge_item).
        missing = [e for e in ids if e not in resolved]
        if missing:
            ph2 = ",".join("?" * len(missing))
            for key, name in conn.execute(
                    f"SELECT key, name FROM nodes WHERE type='Person' AND key IN ({ph2})", missing):
                if name:
                    resolved.setdefault(key, name)
    return {e: resolved.get(e, e.rsplit("/", 1)[-1]) for e in ids}


def _display_name(conn: sqlite3.Connection, entity_id: str) -> str:
    """Single-entity convenience over _display_names (one query, same fallbacks)."""
    return _display_names(conn, [entity_id])[entity_id]


def _named_rows(conn: sqlite3.Connection,
                entity_ids: list[str]) -> list[tuple[str, str]]:
    """(display_name, entity_id) for each id, batch-resolved and sorted by name."""
    names = _display_names(conn, entity_ids)
    return sorted((names[e], e) for e in names)


def faculty_in_department(conn: sqlite3.Connection, org_id: int) -> list[tuple[str, str]]:
    """All faculty (name, entity_id) under a department, sorted by name.

    Union of two sources so it works for BOTH crawled and manually-seeded people:
      • KB-derived — entity_ids with an active knowledge_item filed under this dept (crawled
        profiles), constrained to entity_ids that resolve to an active Person node — a dept
        can also have non-person prose (e.g. a program-info doc) filed under the same org_id,
        which must not surface as a fake faculty member; and
      • graph-derived — person keys with a faculty `has_role` edge to this org (covers people
        seeded with no KB items, e.g. Theatre, whose page lacks the profile template).
    """
    kb = {e for (e,) in conn.execute(
        "SELECT DISTINCT json_extract(metadata,'$.entity_id') FROM knowledge_items "
        "WHERE is_active=1 AND org_id=? AND json_extract(metadata,'$.entity_id') IS NOT NULL "
        "AND json_extract(metadata,'$.entity_id') IN "
        "(SELECT key FROM nodes WHERE type='Person' AND is_active=1)",
        (org_id,)).fetchall()}
    edges = {k for (k,) in conn.execute(
        "SELECT DISTINCT p.key FROM edges e JOIN nodes p ON p.id=e.src_id "
        "JOIN nodes o ON o.id=e.dst_id "
        "WHERE json_extract(o.attrs,'$.org_id')=? AND e.type='has_role' AND e.is_active=1 "
        "AND e.category='faculty'", (org_id,)).fetchall()}
    return _named_rows(conn, list(kb | edges))


def officers_in_org(conn: sqlite3.Connection, org_id: int) -> list[tuple[str, str, str | None]]:
    """(name, title, email) for every active officer/DepRep/administrator appointed directly to
    this org.

    Queries the graph `has_role` edges (category 'officer'/'deprep' for GSA/clubs, or 'admin' for
    university/college leadership — President, Provost, Deans) whose target Org node bridges this
    exact ``org_id`` (NOT descendants — GSA officers are distinct from an RGO's officers; the NJIT
    President sits on the `njit` root while the rest of the cabinet sits on `njit-administration`).
    Title is the first entry in the edge's ``attrs.titles`` (falls back to the category); email from
    the Person node's attrs. Sorted by name."""
    rows = conn.execute(
        "SELECT p.name, e.attrs, e.category, p.attrs FROM edges e "
        "JOIN nodes p ON p.id=e.src_id "
        "JOIN nodes o ON o.id=e.dst_id AND o.is_active=1 "
        "WHERE e.type='has_role' AND e.is_active=1 AND p.is_active=1 "
        "AND e.category IN ('officer','deprep','admin') "
        "AND json_extract(o.attrs,'$.org_id')=?",
        (org_id,)).fetchall()
    out: list[tuple[str, str, str | None]] = []
    for name, eattrs, category, pattrs in rows:
        titles = (json.loads(eattrs) if eattrs else {}).get("titles") or []
        email = (json.loads(pattrs) if pattrs else {}).get("email")
        out.append((name, titles[0] if titles else category, email))
    return sorted(set(out), key=lambda r: r[0])


def people_in_org(conn: sqlite3.Connection, org_id: int) -> list[tuple[str, str, str | None]]:
    """(name, title, email) for EVERY active person with any role directly in this org —
    not just officers (cf. officers_in_org). Answers 'who works at/in <org>'. Title is the
    first of the edge's attrs.titles (falls back to category); email from the Person node.
    Sorted by name."""
    rows = conn.execute(
        "SELECT p.name, e.attrs, e.category, p.attrs FROM edges e "
        "JOIN nodes p ON p.id=e.src_id "
        "JOIN nodes o ON o.id=e.dst_id AND o.is_active=1 "
        "WHERE e.type='has_role' AND e.is_active=1 AND p.is_active=1 "
        "AND json_extract(o.attrs,'$.org_id')=?",
        (org_id,)).fetchall()
    out: list[tuple[str, str, str | None]] = []
    for name, eattrs, category, pattrs in rows:
        titles = (json.loads(eattrs) if eattrs else {}).get("titles") or []
        email = (json.loads(pattrs) if pattrs else {}).get("email")
        out.append((name, titles[0] if titles else category, email))
    return sorted(set(out), key=lambda r: r[0])


def top_people_by_metric(conn: sqlite3.Connection, org_id: int, field_key: str,
                         metric_key: str) -> dict:
    """Rank the DISTINCT active people in the org SUBTREE who have ``profiles[field_key][metric_key]``,
    highest first (ties broken by name). Returns the FULL ranked list (the n-slice + tie handling is
    done at format time — the list is small, ≤ with_metric) plus the two counts that drive the
    honest-partial wording:
      ranked        : [(name, int value), …] — only people who HAVE the metric, desc
      with_metric   : how many distinct people had the metric
      total_in_org  : how many distinct people are in the subtree (any role) — the denominator gap

    A person with several roles in the subtree counts ONCE (GROUP BY / COUNT(DISTINCT p.id)). The
    JSON path is built from registry keys (field_key/metric_key from match_metric), never raw user
    text, and bound as a parameter."""
    ids = sorted(org_descendants(conn, org_id))
    if not ids:
        return {"ranked": [], "with_metric": 0, "total_in_org": 0}
    ph = ",".join("?" * len(ids))
    path = f"$.profiles.{field_key}.{metric_key}"
    member_join = (
        "FROM edges e JOIN nodes p ON p.id=e.src_id "
        "JOIN nodes o ON o.id=e.dst_id AND o.is_active=1 "
        "WHERE e.type='has_role' AND e.is_active=1 AND p.is_active=1 "
        f"AND json_extract(o.attrs,'$.org_id') IN ({ph})")
    rows = conn.execute(
        f"SELECT p.name, json_extract(p.attrs, ?) AS v {member_join} "
        f"AND json_extract(p.attrs, ?) IS NOT NULL "
        f"GROUP BY p.id ORDER BY CAST(json_extract(p.attrs, ?) AS INTEGER) DESC, p.name ASC",
        (path, *ids, path, path)).fetchall()
    ranked = [(normalize_person_name(n), int(v)) for n, v in rows]
    total = conn.execute(
        f"SELECT COUNT(DISTINCT p.id) {member_join}", tuple(ids)).fetchone()[0]
    return {"ranked": ranked, "with_metric": len(ranked), "total_in_org": total}


# A trailing redundant facet word on an extracted area ("graph research", "graph research areas").
# Stripping it is the fallback when the full phrase matches nobody — see _research_entities. The
# leading \s+ guarantees it only ever drops a TRAILING word, never a bare/leading "research".
_FACET_SUFFIX = re.compile(r"\s+research(?:\s+areas?)?$", re.I)


def _expand_llm(conn: sqlite3.Connection, area: str) -> set[str]:
    """Seam over area_expand.expand_area_llm (monkeypatchable in tests; function-level import
    avoids a cycle). Fail-safe: expand_area_llm itself never raises — any error yields an empty
    set, so callers here are automatically a no-op on failure (exact-match behavior preserved)."""
    from v2.core.retrieval.area_expand import expand_area_llm
    return expand_area_llm(conn, area)


def _people_by_verified_tags(conn: sqlite3.Connection, verified: set[str],
                             org_id: int | None) -> dict[str, str]:
    """entity_id -> the person's OWN matched sibling tag (first seen), for every person who
    lists one of the LLM-verified sibling tags. Empty verified set -> {} (no-op)."""
    if not verified:
        return {}
    targets = {t.casefold() for t in verified}
    out: dict[str, str] = {}
    for val, eid in _area_rows(conn, org_id):
        if val.casefold() in targets and eid not in out:
            out[eid] = val
    return out


def _research_entities(conn: sqlite3.Connection, area: str, org_id: int | None,
                       expand: bool = False) -> set[str]:
    def _lookup(term: str) -> set[str]:
        params: list = [_fts_query(term), *_RESEARCH_TYPES]
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

    result = _lookup(area)
    # Fallback (backlog #6A): "graph research" matches nobody as a phrase, but the user means the area
    # "graph". ONLY when the full term is empty AND ends in a redundant facet word, retry once on the
    # stripped term — so a legit field like "operations research" (which resolves) is never broadened.
    if not result:
        stripped = _FACET_SUFFIX.sub("", area or "").strip()
        if stripped and stripped.lower() != (area or "").strip().lower():
            result = _lookup(stripped)
    if expand:
        # LLM-verified area expansion (R1): union in everyone who lists a sibling tag the LLM
        # confirmed belongs to the same field (e.g. "cyber security" -> also "system security").
        # Fail-safe: expand_area_llm returns {} on any error, so this is a no-op then.
        verified = _expand_llm(conn, area)
        result = result | set(_people_by_verified_tags(conn, verified, org_id).keys())
    return result


def people_by_research_area(conn: sqlite3.Connection, area: str,
                            org_id: int | None = None) -> list[tuple[str, str]]:
    """All faculty (name, entity_id) whose research matches ``area`` (FTS word-boundary) OR an
    LLM-verified sibling tag under the same field (R1 area expansion — e.g. "cyber security"
    also surfaces someone who only lists "system security"), optionally scoped to an org
    subtree. Complete and stable — no top-K."""
    return _named_rows(conn, list(_research_entities(conn, area, org_id, expand=True)))


def count_people_by_research_area(conn: sqlite3.Connection, area: str,
                                  org_id: int | None = None) -> int:
    """Count of distinct faculty matching ``area`` (same expand=True population as the list,
    so they can never disagree)."""
    return len(_research_entities(conn, area, org_id, expand=True))


def people_by_research_area_annotated(conn: sqlite3.Connection, area: str,
                                      org_id: int | None = None) -> list[tuple[str, str, str]]:
    """(name, entity_id, matched_tag) for the same population as people_by_research_area —
    exact hits are tagged with the query ``area`` itself; expanded-only hits are tagged with the
    person's OWN verified sibling tag (e.g. "Neamtiu (system security)" for a "cyber security"
    query). Sorted by name. Task 8 (rendering) consumes this."""
    exact = _research_entities(conn, area, org_id, expand=False)
    verified = _expand_llm(conn, area)
    sib = _people_by_verified_tags(conn, verified, org_id)      # eid -> a matched sibling tag
    eids = exact | set(sib.keys())
    named = {eid: name for name, eid in _named_rows(conn, list(eids))}
    out = []
    for eid in eids:
        tag = area if eid in exact else sib.get(eid, area)
        out.append((named.get(eid, eid), eid, tag))
    return sorted(out, key=lambda r: r[0].casefold())


def does_person_research_area(conn: sqlite3.Connection, entity_id: str, area: str,
                              name: str | None = None) -> dict:
    """Yes/no/related: does ONE person research ``area``? Membership never CONTRADICTS
    people_by_research_area's (expanded) roster: an EXACT hit (``entity_id`` ∈
    ``_research_entities(expand=False)``) answers 'yes'; someone who only holds an LLM-verified
    SIBLING tag (in the roster via expansion, but not an exact hit) answers 'related' — never a
    false 'no' for someone the population skill would surface. `basis` records whether a 'yes' is
    a discrete area TAG (matched via the same expand_area synonyms) or profile PROSE (statement/
    overview), so the renderer never over-claims a 'listed' area for a prose-only match.
    Honest-partial ('unknown', never a false 'no') when the person lists no areas at all and holds
    no verified sibling tag either. `research_of_person` supplies the person's own areas for the
    answer text ONLY — never as the source of truth for the yes/no."""
    in_set = entity_id in _research_entities(conn, area, org_id=None, expand=False)
    prof = research_of_person(conn, entity_id)          # {name, areas, statement}
    pats = [re.compile(r"\b" + re.escape(t.casefold()) + r"\b")
            for t in expand_area(area) if (t or "").strip()]
    matched = next((pa for pa in prof["areas"]
                    if any(p.search(pa.casefold()) for p in pats)), None)
    if in_set:
        answer = "yes"
        basis = "tag" if matched else "prose"
        matched_area = matched if basis == "tag" else None
    else:
        sib = _people_by_verified_tags(conn, _expand_llm(conn, area), None)
        if entity_id in sib:
            answer, basis, matched_area = "related", "related", sib[entity_id]
        else:
            answer = "no" if prof["areas"] else "unknown"
            basis, matched_area = None, None
    return {"entity_id": entity_id, "name": name or prof["name"], "area": area,
            "answer": answer, "basis": basis,
            "matched_area": matched_area,
            "person_areas": prof["areas"]}


def is_listed_research_area(conn: sqlite3.Connection, area: str,
                            org_id: int | None = None) -> bool:
    """True iff ≥1 person LISTS ``area`` (or a synonym) as a research-area TAG VALUE — a WORD-BOUNDARY
    match INSIDE a `metadata.areas` tag (so "neuroscience" hits the tag "computational neuroscience"),
    read from the tags directly via `_area_rows` (NEVER search_text) so a person NAME or an incidental
    token can't validate. The A15 loose-area validator: a topic is 'real' iff someone tags it. Because a
    matched tag lives on a `type='research_areas'` item, its search_text contains the term too → the
    skill's FTS (people_by_research_area, a superset over 3 types) is guaranteed to return ≥1 (the ⊂
    guarantee). Uses the same expand_area synonyms as the skill."""
    pats = [re.compile(r"\b" + re.escape(t.casefold()) + r"\b")
            for t in expand_area(area) if (t or "").strip()]
    if not pats:
        return False
    for val, _eid in _area_rows(conn, org_id):
        v = (val or "").casefold()
        if any(p.search(v) for p in pats):
            return True
    return False


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
         "AND json_extract(k.metadata,'$.entity_id') IS NOT NULL" + clause +
         " ORDER BY je.value")   # stable order → deterministic first-seen sibling tag (area-expansion M3)
    out: list[tuple[str, str]] = []
    for val, eid in conn.execute(q, params):
        if val and val.strip() and eid:
            out.append((val.strip(), eid))
    return out


from v2.core.graph.project import canonical_area as _canonical  # shared display-casing picker


def areas_in_org(conn: sqlite3.Connection, org_id: int) -> list[str]:
    """Distinct research areas across an org subtree, case-folded for grouping and shown
    in a canonical casing. The new enumerable facet ('what areas does CS cover?'). Derived
    from area_counts so the two facets can never disagree on the area set."""
    return sorted((a for a, _ in area_counts(conn, org_id)), key=str.casefold)


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


def faculty_areas_in_department(conn: sqlite3.Connection,
                                org_id: int) -> list[tuple[str, list[str]]]:
    """(name, [areas]) for each person in the org subtree who LISTS research areas — grouped
    from research_areas items (metadata.areas), case-fold-deduped per person, sorted by name.

    ONLY people who actually list areas appear (no roster left-join): the honest answer to
    "the research areas of the professors in X" when per-person coverage is partial. When NOBODY
    lists areas this returns [] and the caller renders the honest fallback (faculty names + a
    'no areas listed' line) — see structured_answer. Never invents an area for a name."""
    per: dict[str, dict[str, list[str]]] = {}
    for val, eid in _area_rows(conn, org_id):
        per.setdefault(eid, {}).setdefault(val.casefold(), []).append(val)
    names = _display_names(conn, list(per.keys()))
    out = [(names[eid],
            sorted((_canonical(forms) for forms in groups.values()), key=str.casefold))
           for eid, groups in per.items()]
    return sorted(out, key=lambda t: t[0].casefold())


def people_by_area_tag(conn: sqlite3.Connection, area: str,
                       org_id: int | None = None) -> list[tuple[str, str]]:
    """Faculty (name, entity_id) who LIST ``area`` as a research-area tag — exact
    (case-folded) match against metadata.areas, with P2 expansion so 'ml'/'llm' hit the
    canonical tags. Precise, lower-recall (only faculty who list discrete areas)."""
    targets = {p.casefold() for p in expand_area(area)}
    eids = {eid for val, eid in _area_rows(conn, org_id) if val.casefold() in targets}
    return _named_rows(conn, list(eids))
