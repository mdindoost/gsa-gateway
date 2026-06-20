"""Entity-centric retrieval: person resolution, relational skills, and entity cards.

Phase 1+2 of the retrieval-completeness redesign (docs/superpowers/specs/
2026-06-18-retrieval-completeness-relational-queries-design.md, senior-reviewed).

Fixes the query shapes semantic top-K can't serve:
  - role lookups   ("who is the dean of YWCC")        -> role_in_org (exact head match)
  - name lists     ("list all the Michaels")          -> people_by_name (complete)
  - person research("Guiling Wang research")          -> research_of_person (clean tags)
  - "tell me about X" / "X's email" (multi-doc split) -> entity_card (full person context)
  - ambiguous name ("professor Wang")                 -> resolve_people -> disambiguation

Design rules (from review, verified against gsa_gateway.db):
- A "person" is a Person NODE (nodes.type='Person', key=entity_id). Resolution always
  starts from nodes — never from KB entity_ids (169 KB entity_ids have no Person node).
- Names are inconsistent: 'Halper, Michael' (crawler) vs 'Guiling Wang' (dashboard).
  normalize_person_name() canonicalizes to 'First Last' for ALL display + sorting.
- role_in_org matches the title HEAD exactly: 'dean' matches 'Dean, YWCC' but NOT
  'Associate Dean…'. When the exact role is absent (e.g. no 'Chair' exists for
  Informatics) it returns [] and the caller FALLS THROUGH to RAG — never names an
  associate-holder as "the X".
- entity_card EXCLUDES publication/webpage (a person has 90+ papers; never dump them).
- All functions are read-only SELECTs; the caller owns the connection.
"""

from __future__ import annotations

import json
import re
import sqlite3

from v2.core.graph.project import area_key, canonical_area

from v2.core.people import profile_fields

# Lower rank = preferred for the "primary" role shown in disambiguation.
_ROLE_RANK = {"faculty": 0, "admin": 1, "officer": 2, "joint": 3,
              "staff": 4, "advisor": 5, "emeritus": 6}

# Title types whose text we fold into an entity card (publication/webpage excluded).
_CARD_DOC_TYPES = ("about", "research_statement", "education", "teaching", "service")


def normalize_person_name(name: str) -> str:
    """'Halper, Michael' -> 'Michael Halper'; 'Guiling Wang' -> 'Guiling Wang'."""
    n = (name or "").strip()
    if "," in n:
        parts = [p.strip() for p in n.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            return f"{parts[1]} {parts[0]}"
    return n


def _name_tokens(name: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]+", (name or "").lower()) if len(t) > 1]


def _word_in(token: str, text_lower: str) -> bool:
    return re.search(r"\b" + re.escape(token) + r"\b", text_lower) is not None


def _primary_role(conn: sqlite3.Connection, node_id: int) -> tuple[str | None, str | None]:
    """(title, org_name) of the person's best role, for display/disambiguation."""
    best = 99
    title, org = None, None
    for eattrs, cat, oname in conn.execute(
            "SELECT e.attrs, e.category, o.name FROM edges e JOIN nodes o ON o.id=e.dst_id "
            "WHERE e.src_id=? AND e.type='has_role' AND e.is_active=1", (node_id,)):
        rank = _ROLE_RANK.get(cat, 50)
        if rank < best:
            best = rank
            titles = (json.loads(eattrs) if eattrs else {}).get("titles") or []
            title = titles[0] if titles else cat
            org = oname
    return title, org


def resolve_people(conn: sqlite3.Connection, name_query: str) -> list[dict]:
    """Active Person nodes whose name contains EVERY query token (word-boundary,
    case-insensitive). COMPLETE set (powers enumeration AND disambiguation).
    Each hit: {entity_id, name (normalized), title, org}. Sorted by name."""
    toks = _name_tokens(name_query)
    if not toks:
        return []
    hits: list[dict] = []
    for nid, key, raw in conn.execute(
            "SELECT id, key, name FROM nodes WHERE type='Person' AND is_active=1"):
        nl = raw.lower()
        if all(_word_in(t, nl) for t in toks):
            title, org = _primary_role(conn, nid)
            hits.append({"entity_id": key, "name": normalize_person_name(raw),
                         "title": title, "org": org})
    return sorted(hits, key=lambda d: d["name"])


def people_by_name(conn: sqlite3.Connection, name: str) -> list[dict]:
    """Complete roster of people whose name matches — never top-K. Same as resolve_people;
    named separately for routing/answer clarity."""
    return resolve_people(conn, name)


def persons_in_query(conn: sqlite3.Connection, q: str) -> list[dict]:
    """People whose FULL name appears in the query (every name-token of the person is a
    word in q). Strong, low-false-positive: 'guiling wang research' -> [Guiling Wang];
    'professor wang' -> [] (no full name). Used to detect a named entity in free text."""
    ql = q.lower()
    out: list[dict] = []
    for nid, key, raw in conn.execute(
            "SELECT id, key, name FROM nodes WHERE type='Person' AND is_active=1"):
        toks = _name_tokens(raw)
        if len(toks) >= 2 and all(_word_in(t, ql) for t in toks):
            title, org = _primary_role(conn, nid)
            out.append({"entity_id": key, "name": normalize_person_name(raw),
                        "title": title, "org": org})
    return sorted(out, key=lambda d: d["name"])


def persons_by_lastname(conn: sqlite3.Connection, token: str) -> list[dict]:
    """People whose LAST name == token (normalized 'First Last'). For surname-only
    disambiguation ('professor Wang' -> all Wangs). Returns [] for a non-surname token."""
    t = token.strip().lower()
    if len(t) < 2:
        return []
    out: list[dict] = []
    for nid, key, raw in conn.execute(
            "SELECT id, key, name FROM nodes WHERE type='Person' AND is_active=1"):
        last = normalize_person_name(raw).split()[-1].lower() if raw else ""
        if last == t:
            title, org = _primary_role(conn, nid)
            out.append({"entity_id": key, "name": normalize_person_name(raw),
                        "title": title, "org": org})
    return sorted(out, key=lambda d: d["name"])


# A title segment whose LEAD role is support staff — so a trailing role in their title is a
# SCOPE descriptor, not their role (e.g. "Executive Assistant, Dean of Students" is an assistant,
# not the dean). Used to avoid naming support staff as the role-holder.
_SUPPORT_LEAD = re.compile(
    r"^(?:executive\s+|administrative\s+|senior\s+)?"
    r"(?:assistant|aide|secretary|coordinator|specialist)\b", re.I)


def people_by_role(conn: sqlite3.Connection, role_head: str,
                   org_id: int | None = None) -> list[tuple[str, str, str, str | None]]:
    """(name, title, org_name, contact) for everyone whose has_role title carries ``role_head``
    as the head of a title SEGMENT — across the whole graph, or within ONE org if ``org_id`` is
    given. This is the single role-lookup path (org-agnostic by design): you find the provost,
    a dean, a chair, etc. by their ROLE, not by where they're filed.

    Matching rules (the real correctness content):
      • Exact head match per segment: 'provost' matches a segment starting 'Provost' but NOT
        'Vice Provost' / 'Associate Provost'.
      • Compound titles are split on ',' and ' and ', so 'Senior VP of Student Affairs and Dean
        of Students' matches 'dean of students'.
      • If the matching segment is NOT the lead and the lead is a SUPPORT role, skip — an
        'Executive Assistant, Dean of Students' is not the dean.
    Empty list → caller falls through to RAG (never invents)."""
    rx = re.compile(r"^" + re.escape(role_head.strip().lower()) + r"\b")
    # A leading SCOPE word ("Department Chair", "Departmental Chair") is not a rank modifier — strip
    # it so "chair" matches "Department Chair", while a RANK modifier (Vice/Associate Chair) still
    # won't match (it isn't a scope word).
    _scope = re.compile(r"^(?:departmental|department)\s+", re.I)
    sql = ("SELECT p.name, e.attrs, p.attrs, o.name FROM edges e JOIN nodes p ON p.id=e.src_id "
           "JOIN nodes o ON o.id=e.dst_id AND o.is_active=1 "
           "WHERE e.type='has_role' AND e.is_active=1 AND p.is_active=1")
    params: list = []
    if org_id is not None:
        sql += " AND json_extract(o.attrs,'$.org_id')=?"
        params.append(org_id)
    out: list[tuple[str, str, str, str | None]] = []
    for raw, eattrs, pattrs, oname in conn.execute(sql, params):
        titles = (json.loads(eattrs) if eattrs else {}).get("titles") or []
        pa = json.loads(pattrs) if pattrs else {}
        contact = pa.get("email") or pa.get("phone")
        for title in titles:
            segs = [s.strip() for s in re.split(r",|\s+and\s+", title) if s.strip()]
            idx = next((i for i, s in enumerate(segs)
                        if rx.match(s.lower()) or rx.match(_scope.sub("", s.lower()))), None)
            if idx is None:
                continue
            if idx > 0 and segs and _SUPPORT_LEAD.match(segs[0]):
                continue   # support-staff lead → the role is just a scope descriptor
            out.append((normalize_person_name(raw), title, oname, contact))
            break
    return sorted(set(out), key=lambda r: (r[0], r[2]))


def role_in_org(conn: sqlite3.Connection, org_id: int,
                role_head: str) -> list[tuple[str, str, str | None]]:
    """(name, title, email) for people in THIS org whose has_role title head is ``role_head``.
    Thin org-scoped wrapper over people_by_role (drops the org column). Empty → RAG."""
    return [(n, t, c) for (n, t, _o, c) in people_by_role(conn, role_head, org_id=org_id)]


def research_of_person(conn: sqlite3.Connection, entity_id: str) -> dict:
    """{name, areas, statement} for one person — the UNION of every active research_areas KB item
    (crawler + scholar) AND every active `researches` edge, deduped by area_key and rendered in a
    canonical display casing. Self-asserted (scholar) areas that are a token-subset of a specific
    area from another source are suppressed from the DISPLAY (e.g. broad 'databases' under
    'Multimedia Databases') to avoid garbled lists. Honest-empty when the person has none."""
    row = conn.execute(
        "SELECT name FROM nodes WHERE type='Person' AND key=? AND is_active=1",
        (entity_id,)).fetchone()
    name = normalize_person_name(row[0]) if row else entity_id

    raw: list[tuple[str, bool]] = []   # (surface form, is_scholar_sourced)
    for areas_json, asrc in conn.execute(
            "SELECT json_extract(metadata,'$.areas'), json_extract(metadata,'$.area_source') "
            "FROM knowledge_items WHERE is_active=1 AND type='research_areas' "
            "AND json_extract(metadata,'$.entity_id')=?", (entity_id,)):
        try:
            items = json.loads(areas_json) if areas_json else []
        except (TypeError, ValueError):
            items = []
        for a in items:
            if a and a.strip():
                raw.append((a.strip(), asrc == "scholar"))
    for aname, e_asrc in conn.execute(
            "SELECT ra.name, e.area_source FROM edges e JOIN nodes p ON p.id=e.src_id "
            "JOIN nodes ra ON ra.id=e.dst_id "
            "WHERE p.key=? AND e.type='researches' AND e.is_active=1", (entity_id,)):
        if aname and aname.strip():
            raw.append((aname.strip(), e_asrc == "external"))

    groups: dict[str, dict] = {}       # area_key -> {forms:[...], has_other_source:bool}
    for form, is_scholar in raw:
        g = groups.setdefault(area_key(form), {"forms": [], "has_other_source": False})
        g["forms"].append(form)
        if not is_scholar:
            g["has_other_source"] = True
    # subsumption: drop a scholar-ONLY area whose tokens are a proper subset of a specific
    # area contributed by another (non-scholar) source.
    drop: set[str] = set()
    for k, g in groups.items():
        if g["has_other_source"]:
            continue
        ktoks = set(k.split())
        for k2, g2 in groups.items():
            if k2 != k and g2["has_other_source"] and ktoks < set(k2.split()):
                drop.add(k)
                break
    areas = sorted((canonical_area(g["forms"]) for k, g in groups.items() if k not in drop),
                   key=str.casefold)
    statement = None
    rs = conn.execute(
        "SELECT content FROM knowledge_items WHERE is_active=1 AND type='research_statement' "
        "AND json_extract(metadata,'$.entity_id')=? LIMIT 1", (entity_id,)).fetchone()
    if rs and rs[0]:
        statement = rs[0].strip()
    return {"name": name, "areas": areas, "statement": statement}


def person_attrs(conn: sqlite3.Connection, entity_id: str) -> dict:
    """The Person node's parsed ``attrs`` bag (or {}). The SINGLE JSON-reader for per-person
    attrs — structured_answer delegates here so there is only one read path."""
    row = conn.execute(
        "SELECT attrs FROM nodes WHERE type='Person' AND key=? AND is_active=1",
        (entity_id,)).fetchone()
    if not row or not row[0]:
        return {}
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return {}


def metric_of_person(conn: sqlite3.Connection, entity_id: str, field_key: str,
                     metric_key: str | None = None) -> dict:
    """{name, field_key, found, all, updated_at} for one person's numeric metrics, read straight
    from ``attrs.profiles[field_key]``. ``found`` is the asked metric (or all of the field's metrics
    when ``metric_key`` is None); ``all`` is every metric present for the field — so a partial miss
    (asked h-index, only citations on file) can still offer what we DO have. Honest-empty (found={})
    when absent. Never fabricated."""
    attrs = person_attrs(conn, entity_id)
    row = conn.execute(
        "SELECT name FROM nodes WHERE type='Person' AND key=? AND is_active=1",
        (entity_id,)).fetchone()
    name = normalize_person_name(row[0]) if row else entity_id
    entry = ((attrs.get("profiles") or {}).get(field_key)) or {}
    metric_keys = [m.key for fk, m in profile_fields.metric_fields() if fk == field_key]
    all_present = {k: entry.get(k) for k in metric_keys if entry.get(k) is not None}
    if metric_key is None:
        found = dict(all_present)
    elif entry.get(metric_key) is not None:
        found = {metric_key: entry[metric_key]}
    else:
        found = {}
    return {"name": name, "field_key": field_key, "found": found, "all": all_present,
            "updated_at": entry.get("updated_at")}


def entity_card(conn: sqlite3.Connection, entity_id: str) -> str:
    """A complete grounded fact block for ONE person (KG roles + email + research +
    education/about/teaching prose), EXCLUDING publication/webpage. Returned as text for
    LLM grounding (and the offline fallback). '' if the person isn't found."""
    node = conn.execute(
        "SELECT id, name, attrs FROM nodes WHERE type='Person' AND key=? AND is_active=1",
        (entity_id,)).fetchone()
    if not node:
        return ""
    nid, raw, nattrs = node
    attrs = json.loads(nattrs) if nattrs else {}
    lines = [normalize_person_name(raw)]

    seen: set[str] = set()
    for eattrs, cat, oname in conn.execute(
            "SELECT e.attrs, e.category, o.name FROM edges e JOIN nodes o ON o.id=e.dst_id "
            "WHERE e.src_id=? AND e.type='has_role' AND e.is_active=1 ORDER BY o.name", (nid,)):
        titles = (json.loads(eattrs) if eattrs else {}).get("titles") or [cat]
        for t in titles:
            line = f"{t} — {oname}"
            if line not in seen:
                seen.add(line)
                lines.append(line)
    if attrs.get("email"):
        lines.append(f"Email: {attrs['email']}")
    if attrs.get("phone"):
        lines.append(f"Phone: {attrs['phone']}")

    rp = research_of_person(conn, entity_id)
    if rp["areas"]:
        lines.append("Research areas: " + "; ".join(rp["areas"]))

    for typ in _CARD_DOC_TYPES:
        for (content,) in conn.execute(
                "SELECT content FROM knowledge_items WHERE is_active=1 AND type=? "
                "AND json_extract(metadata,'$.entity_id')=?", (typ, entity_id)):
            if content and content.strip():
                lines.append(content.strip())
    return "\n".join(lines)
