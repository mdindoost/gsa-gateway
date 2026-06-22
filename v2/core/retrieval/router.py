"""Deterministic structured-query router (Phase 1).

Maps a question to (skill, resolved args) ONLY when it is clearly a structured
ask — enumerate / filter / traverse / count. Otherwise returns None, and the caller
falls through to the unchanged semantic-RAG path. Conservative by design: a
descriptive question forced into a skill (false positive) is the dangerous failure,
so anything that doesn't clearly match — or whose org/area doesn't resolve — returns
None (semantic RAG is the safe default).

No LLM here (the local 8B is unreliable at orchestration); routing + slot extraction
are rule-based. See docs/superpowers/specs/2026-06-13-structured-retrieval-phase1.md.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass

from v2.core.people import profile_fields
from v2.core.retrieval import entity, skills

# Verb phrases that introduce a research area ("who WORKS ON graph"). Deliberately
# specific — bare "research"/"on" must NOT trigger (e.g. "what research does X involve").
_AREA_TRIGGER = re.compile(
    r"(?:works?\s+on|working\s+on|researches|researching|research\s+(?:in|on)|"
    r"researchers?\s+(?:in|on|of)|studies|studying|specializ(?:es|ing)\s+in|"
    r"expert(?:ise)?\s+in)\s+(.+)")

# Enumeration of the research-area facet ("what research areas does CS cover").
_ENUM_AREAS = re.compile(
    r"\b(?:research areas?|areas? of research|"
    r"(?:what|which|list|all|show)\s+(?:research\s+)?areas?)\b")
# Ranking/aggregation cue ("which areas have the MOST faculty").
_RANK = re.compile(r"\b(?:most|top|popular|biggest|largest|ranked|by count|how many people)\b")
# Enumerate an org's SUB-departments ("what departments are in YWCC"). Requires the plural OR an
# explicit enumeration verb ("what/which/how many/list department(s)") — so naming "the math
# department" (the org itself) does NOT get read as "list math's sub-departments" (which, for a
# leaf dept, falsely deflects to 'no info'). The singular enumeration form is included so
# "what department does NCE have" still routes.
_DEPT_ENUM = re.compile(
    r"\bsub-?departments?\b|\bdepartments\b|"
    r"\b(?:what|which|list|how\s+many)\s+(?:sub-?)?departments?\b")
# "who LISTS X as a research area" -> precise tag match.
_LISTS_AREA = re.compile(r"who\s+lists?\s+(.+?)\s+as\s+(?:an?\s+)?research\s+area")

# Officer-IDENTITY ask only: "who is/are/'s the <title>", "list/name/show the officers".
# A bare mention of "officer" in a process question (impeach / elect / duties / eligibility)
# must NOT route here — it falls through to RAG (the constitution). Excludes professor/faculty
# so it never hijacks the YWCC faculty branch.
_OFFICER_TITLE = (
    r"(?:officers?|e-?board|executive board|president|vice[- ]president|\bvp\b|"
    r"treasurer|secretary|deprep|department representatives?)")
# Positive identity structure: a who/list trigger, then up to 3 modifier words (determiner +
# org name, e.g. "the GWICS"), then the title. The short window keeps process phrasings out
# (their title sits too far after the trigger, e.g. "who is eligible to be an officer").
_OFFICER_IDENTITY = re.compile(
    r"(?:who(?:\s+(?:is|are)|'?s)|\b(?:list|name|show))\s+"
    r"(?:[a-z0-9'\-]+\s+){0,3}?"
    + _OFFICER_TITLE)
# Secondary guard: even if the identity shape matches, a process/relational verb means it is
# NOT an identity ask ("who is responsible for officer elections") → fall through to RAG.
_OFFICER_PROCESS = re.compile(
    r"\b(?:impeach|elect|appoint|nominat|remov|dismiss|replac|becom|eligib|qualif|"
    r"responsib|dut(?:y|ies)|chosen|select|how\s+many)")

# "who works at/in <org>", "people in <org>", "<org> staff/team" -> the full roster.
_PEOPLE = re.compile(
    r"\b(who works?\b|works? (?:at|in|for)\b|people (?:in|at|of)\b|"
    r"staff (?:of|at|in)\b|team (?:of|at|in)\b|members? of\b|"
    r"administrat(?:or|ors|ion)\b|leadership\b|cabinet\b)")

# ── person-/role-centric (Phase 1+2) ────────────────────────────────────────────
# Academic-leadership role HEADS that exist as has_role edge titles. president/vice-
# president/chief/provost are deliberately excluded (officer-handled, or absent as a
# standalone title). Order longest-first so "associate dean" beats "dean".
_ROLE_HEAD = (r"associate\s+dean|assistant\s+dean|associate\s+chair|"
              r"dean|chair|director|coordinator|head")
_ROLE_OF_ORG = re.compile(r"\b(" + _ROLE_HEAD + r")\s+(?:of|at|for|in)\b")

# Role-identity vocabulary for "who is the <role>" / "the <role> of <org>" / "who are the <role>s".
# Longest-first so multi-word roles win ("dean of students" before "dean"). Officer titles
# (president/vice president/treasurer/secretary) are handled by the OFFICER branch above and are
# deliberately excluded here. Synonyms map to how the title actually reads on the edge.
_ROLE_VOCAB = ["associate dean", "assistant dean", "associate chair", "vice provost",
               "associate provost", "dean of students", "general counsel",
               "chief financial officer", "athletic director", "director of athletics",
               "chief of staff", "provost", "chancellor", "dean", "chair", "director",
               "coordinator", "cfo"]
_ROLE_VOCAB_RX = re.compile(
    r"\b(" + "|".join(re.escape(r) for r in sorted(_ROLE_VOCAB, key=len, reverse=True)) + r")s?\b",
    re.I)
_ROLE_SYNONYM = {"cfo": "chief financial officer", "athletic director": "director of athletics"}
# Duties/process/eligibility => NOT an identity ask (mirrors _OFFICER_PROCESS).
_LEADERSHIP_PROCESS = re.compile(
    r"\b(do(?:es)?|responsib|dut(?:y|ies)|how\s+(?:to|do)|become|elect|appoint|"
    r"eligib|qualif|nominat|remov|why|what'?s?\s+the\s+role)\b")
_ENUM_TRIGGER = re.compile(r"\b(?:list|name|show|all|every|any|are\s+there|is\s+there|do\s+we\s+have)\b")
# Faculty-roster cues: any of these + a resolved org → list that department's faculty. Broad on
# purpose (people ask many ways). Deliberately EXCLUDES 'people/members/staff/works' (→
# people_in_org) and 'researchers' (→ research-area queries), so it doesn't hijack those branches.
_FACULTY_CUE = re.compile(
    r"\b(?:faculty|professors?|lecturers?|instructors?|teachers?|academics|"
    r"teaching\s+(?:staff|faculty)|academic\s+staff)\b"
    r"|\bwho\s+teach(?:es)?\b|\bteach(?:es|ing)?\s+(?:in|at|for|within)\b", re.I)
_RESEARCH_CUE = re.compile(r"\b(research|works?\s+on|working\s+on|studies|studying|specializ|expert)\b")
# Ranking cue for a METRIC ranking ("who has the MOST citations", "TOP 5 by h-index"). Narrowed
# (RAG review) — leading/largest/greatest never naturally rank citations, only add false positives.
_RANK_CUE = re.compile(r"\b(most|top|highest|ranked?|rank)\b")
_TOPN = re.compile(r"\btop\s+(\d+)\b|\b(\d+)\s+most\b")
_PERSON_INTENT = re.compile(
    r"\b(who(?:'s|\s+is|\s+are)|tell\s+me\s+about|info(?:rmation)?\s+on|"
    r"profile\s+of|contact\s+(?:for|info)|reach)\b")
_PERSON_ATTR = re.compile(r"\b(e-?mail|office|phone|number|title|position|bio)\b")
# Looser "more about this person" cues that FOLLOW a bare surname ("koutis info",
# "koutis all info", "koutis details") — distinct from _PERSON_INTENT's "info ON <name>".
_INFO_CUE = re.compile(r"\b(info|information|details|profile|bio|background|everything)\b")
_NAME_PREFIX = re.compile(r"\b(?:professor|prof\.?|dr\.?|mr\.?|ms\.?|mrs\.?)\b")
_STOP_FOR_ENUM = {
    "list", "name", "names", "show", "all", "every", "any", "are", "there", "is",
    "do", "we", "have", "the", "a", "an", "of", "in", "at", "please", "me", "us",
    "people", "person", "persons", "named", "called", "who", "anyone", "someone"}


def _qtokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]+", text.lower()) if len(t) > 1]


def _has_child_departments(conn, org_id: int) -> bool:
    """True iff this org has active child orgs that org_departments would list (type='department').
    A leaf department (CS, Math) has none — so 'departments in math' must NOT route to
    org_departments (which would emit the misleading 'I don't have department information')."""
    return conn.execute(
        "SELECT 1 FROM organizations WHERE parent_id=? AND is_active=1 AND type='department' LIMIT 1",
        (org_id,)).fetchone() is not None


def _is_university_root(conn, org_id: int) -> bool:
    """True for the university ROOT org (the only org with no parent). people_in_org enumerates
    roles attached DIRECTLY to the org node; for the root that's just the President, so a bare
    'people at njit' is a thin/misleading enumeration — let it fall through to RAG instead."""
    row = conn.execute("SELECT parent_id FROM organizations WHERE id=?", (org_id,)).fetchone()
    return row is not None and row[0] is None


def _is_bare_name(q: str, person: dict) -> bool:
    """True when the (prefix-stripped) query is essentially just this person's name —
    every query token is one of the person's name tokens. 'guiling wang' -> True;
    'guiling wang research' -> False."""
    ptoks = set(_qtokens(person["name"]))
    qtoks = _qtokens(q)
    return bool(qtoks) and all(t in ptoks for t in qtoks)


@dataclass
class Route:
    skill: str
    args: dict


def _org_candidates(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    cands: list[tuple[str, int]] = []
    for oid, name, slug, metadata in conn.execute(
            "SELECT id,name,slug,metadata FROM organizations WHERE is_active=1"):
        nl = name.lower()
        cands.append((nl, oid))
        cands.append((slug.lower(), oid))
        if "-" in slug:
            cands.append((slug.replace("-", " ").lower(), oid))
        # the name with parentheticals stripped: "… Society (GWICS)" -> "… society"
        stripped = re.sub(r"\([^)]*\)", "", name).strip().lower()
        if stripped and stripped != nl:
            cands.append((stripped, oid))
        # parenthetical acronyms/short names: "(GWICS)" -> "gwics" (>=3 chars to avoid noise)
        for paren in re.findall(r"\(([^)]+)\)", name):
            p = paren.strip().lower()
            if len(p) >= 3:
                cands.append((p, oid))
        # admin-declared aliases in organizations.metadata.aliases (JSON list of nicknames)
        try:
            aliases = (json.loads(metadata) if metadata else {}).get("aliases") or []
        except (TypeError, ValueError):
            aliases = []
        for a in aliases:
            a = str(a).strip().lower()
            if a:
                cands.append((a, oid))
    for alias in skills._ORG_ALIASES:
        oid = skills.resolve_org(conn, alias)
        if oid:
            cands.append((alias.lower(), oid))
    return cands


def _find_org(conn: sqlite3.Connection, text: str) -> tuple[int | None, str | None]:
    """Longest org name/slug/alias appearing as a whole word in the text → (id, phrase)."""
    best: tuple[int, str] | None = None
    for phrase, oid in _org_candidates(conn):
        if phrase and re.search(r"\b" + re.escape(phrase) + r"\b", text):
            if best is None or len(phrase) > len(best[1]):
                best = (oid, phrase)
    return best if best else (None, None)


def _extract_area(q: str, org_phrase: str | None) -> str | None:
    m = _AREA_TRIGGER.search(q)
    if not m:
        return None
    area = m.group(1).strip()
    if org_phrase and org_phrase in area:           # drop a trailing "… in <org>"
        area = area.split(org_phrase)[0].strip()
        area = re.sub(r"\s+(in|at|within|of)$", "", area).strip()
    area = area.strip(" .,?")
    return area or None


def _parse_topn(q: str) -> int:
    """N from 'top N' / 'N most'; 1 for a bare 'most'/'highest'."""
    m = _TOPN.search(q)
    if m:
        return int(m.group(1) or m.group(2))
    return 1


def _resolve_surname(conn: sqlite3.Connection, q: str) -> dict | Route | None:
    """Resolve a person by an UNAMBIGUOUS surname token in the (prefix-stripped) query:
    {entity_id, name} for one match, a person_disambig Route for ≥2, or None. The single shared
    surname resolver (used by the research, entity-card, metric, and link branches).

    GUARD (bug 2): only attempt on a SHORT, person-directed query (≤4 content tokens). A long meta
    sentence ("I see you used he for Vincent… everything…") otherwise gets surname-mined token-by-token
    and an incidental word like "see" resolves to a real person (Adam See) → confident wrong-person
    answer. No stoplist — that would break real faculty named Young/White/Brown; length is the fix."""
    stripped = _NAME_PREFIX.sub("", q)
    if len(_qtokens(stripped)) > 4:
        return None
    for tok in _qtokens(stripped):
        cands = entity.persons_by_lastname(conn, tok)
        if len(cands) >= 2:
            return Route("person_disambig", {"candidates": cands})
        if len(cands) == 1:
            return {"entity_id": cands[0]["entity_id"], "name": cands[0]["name"]}
    return None


def _resolve_person(conn: sqlite3.Connection, q: str, named: list[dict]) -> dict | Route | None:
    """The person a question is about: a single {entity_id, name}, a person_disambig Route when
    ambiguous, or None. Tries FULL names found in the query first, then the surname fallback."""
    if len(named) == 1:
        return {"entity_id": named[0]["entity_id"], "name": named[0]["name"]}
    if len(named) > 1:
        return Route("person_disambig", {"candidates": named})
    return _resolve_surname(conn, q)


def route(conn: sqlite3.Connection, question: str) -> Route | None:
    q = question.strip().lower().rstrip("?").strip()
    org_id, org_phrase = _find_org(conn, q)
    area = _extract_area(q, org_phrase)
    named = entity.persons_in_query(conn, q)   # people whose FULL name is in the query

    # precise "who lists X as a research area" (before the generic area branches)
    m = _LISTS_AREA.search(q)
    if m:
        tag = m.group(1).strip()
        if org_phrase and org_phrase in tag:
            tag = tag.split(org_phrase)[0].strip()
        tag = tag.strip(" .,?")
        if tag:
            return Route("people_by_area_tag", {"area": tag, "org_id": org_id})

    if "how many" in q and area:
        return Route("count_people_by_research_area", {"area": area, "org_id": org_id})
    if area:
        return Route("people_by_research_area", {"area": area, "org_id": org_id})

    # enumeration / aggregation over the area facet (org required)
    if org_id is not None and _ENUM_AREAS.search(q):
        if _RANK.search(q):
            return Route("area_counts", {"org_id": org_id})
        # No faculty cue → enumerate the department's area facet ("what areas does CS cover").
        if not _FACULTY_CUE.search(q):
            return Route("areas_in_org", {"org_id": org_id})
        # Faculty cue + area enumeration ("research areas of the professors in X") → per-person
        # areas. The skill renders only people who LIST areas (honest-partial) and degrades to a
        # names roster + 'no areas listed' line when nobody does — so the LLM is never handed a
        # bare name list to invent areas for (the fabrication bug). Anti-fabrication, not RAG.
        return Route("faculty_areas_in_department", {"org_id": org_id})

    # ── metric queries (Scholar citations / h-index / i10) ─────────────────────────
    # Registry-driven (profile_fields.match_metric): only words registered as a Metric alias match,
    # so non-metric uses ("how do I cite a paper", "form i10") don't. Placed AFTER the area branches
    # (so "most cited research area" stays an area question) and BEFORE the generic person branches.
    # Ranking needs an org + a rank cue; single-person needs a resolvable person. A metric word with
    # NEITHER must FALL THROUGH (no return here) so the normal person/RAG branches still run.
    mm = profile_fields.match_metric(q)
    if mm is not None:
        field_key, metric = mm
        if org_id is not None and _RANK_CUE.search(q):
            return Route("top_people_by_metric",
                         {"org_id": org_id, "field_key": field_key,
                          "metric_key": metric.key, "n": _parse_topn(q)})
        person = _resolve_person(conn, q, named)
        if isinstance(person, Route):
            return person
        if isinstance(person, dict):
            return Route("metric_of_person",
                         {"entity_id": person["entity_id"], "name": person["name"],
                          "field_key": field_key, "metric_key": metric.key})

    # ── profile-link queries ("X linkedin / scholar / github / website") ───────────
    # Registry-driven (match_link_field). Needs a resolvable person; a link word with no person
    # ("what's on the GSA website") must FALL THROUGH (no return) to the normal RAG path.
    lm = profile_fields.match_link_field(q)
    if lm is not None:
        field_key, _field = lm
        person = _resolve_person(conn, q, named)
        if isinstance(person, Route):
            return person
        if isinstance(person, dict):
            return Route("link_of_person",
                         {"entity_id": person["entity_id"], "name": person["name"],
                          "field_key": field_key})

    if (org_id is not None and _OFFICER_IDENTITY.search(q)
            and not _OFFICER_PROCESS.search(q)):
        return Route("officers_in_org", {"org_id": org_id})

    # ── role lookup: find a person BY THEIR ROLE ("who is the provost", "the chair of cs",
    # "who are the deans"). Comes BEFORE the department/faculty/people branches so a NAMED role
    # wins over "list <org>'s departments" (e.g. "chair of cs department" is a role ask, not a
    # department list). Officer titles stay with the officer branch above; process/eligibility
    # shapes ("how to become a dean") fall through to RAG. Empty result → RAG.
    if not _LEADERSHIP_PROCESS.search(q):
        rm = _ROLE_VOCAB_RX.search(q)
        if rm and (_PERSON_INTENT.search(q) or _ENUM_TRIGGER.search(q)
                   or _ROLE_OF_ORG.search(q) or org_id is not None):
            role = _ROLE_SYNONYM.get(rm.group(1).lower(), rm.group(1).lower())
            return Route("people_by_role", {"role_head": role, "org_id": org_id})

    # Faculty roster is MORE SPECIFIC than the generic people list, so it wins first — e.g.
    # "academic staff in biology" is a faculty ask even though it contains 'staff' (which the
    # generic _PEOPLE cue also matches).
    if (_DEPT_ENUM.search(q) and org_id is not None and not _FACULTY_CUE.search(q)
            and _has_child_departments(conn, org_id)):
        return Route("org_departments", {"org_id": org_id})
    if org_id is not None and _FACULTY_CUE.search(q):
        return Route("faculty_in_department", {"org_id": org_id})

    if org_id is not None and _PEOPLE.search(q) and not _is_university_root(conn, org_id):
        return Route("people_in_org", {"org_id": org_id})

    # ── person-centric branches (entity layer) ─────────────────────────────────
    # (`named` resolved at the top of route().)

    # name enumeration: "list all the Michaels" / "the Michaels at NJIT"
    if _ENUM_TRIGGER.search(q):
        cand_toks = [t for t in _qtokens(q) if t not in _STOP_FOR_ENUM]
        if org_phrase:                                    # drop org-name tokens ("…at NJIT")
            org_toks = set(_qtokens(org_phrase))
            cand_toks = [t for t in cand_toks if t not in org_toks]
        cand = re.sub(r"s\b", "", " ".join(cand_toks)).strip()   # singularize: michaels→michael
        if cand and 1 <= len(cand.split()) <= 3 and entity.resolve_people(conn, cand):
            return Route("people_by_name", {"name": cand})

    # person → research: "<full name> research / works on / studies". Full name first, then an
    # unambiguous-surname fallback (shared _resolve_person — same path the metric branch uses).
    if _RESEARCH_CUE.search(q):
        person = _resolve_person(conn, q, named)
        if isinstance(person, Route):
            return person
        if isinstance(person, dict):
            return Route("research_of_person",
                         {"entity_id": person["entity_id"], "name": person["name"]})

    # entity card: a specific named person (who-is / tell-me-about / "<name>'s email" /
    # bare name). LAST + most-guarded so it never hijacks a "who works on X" ask.
    qn = _NAME_PREFIX.sub("", q).strip()
    if len(named) == 1 and (_PERSON_INTENT.search(q) or _PERSON_ATTR.search(q)
                            or _is_bare_name(qn, named[0])):
        return Route("entity_card",
                     {"entity_id": named[0]["entity_id"], "name": named[0]["name"]})
    if len(named) > 1 and (_PERSON_INTENT.search(q) or _PERSON_ATTR.search(q)):
        return Route("person_disambig", {"candidates": named})

    # surname-only: "professor Wang" / "who is Wang" / "Koutis's email" / "koutis info" /
    # bare "koutis". The trigger is person-directed (intent / attribute / title prefix / an
    # info cue) OR the whole query is just one token (a lone surname). Resolution is by real
    # last name, so a non-person single word ("events") simply finds nothing and falls to RAG.
    qn_toks = _qtokens(qn)
    if (_PERSON_INTENT.search(q) or _PERSON_ATTR.search(q) or _NAME_PREFIX.search(q)
            or _INFO_CUE.search(q) or len(qn_toks) == 1):
        person = _resolve_surname(conn, q)
        if isinstance(person, Route):
            return person
        if isinstance(person, dict):
            return Route("entity_card",
                         {"entity_id": person["entity_id"], "name": person["name"]})
    return None
