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

from rapidfuzz import fuzz

from v2.core.people import profile_fields
from v2.core.retrieval import entity, skills

# Verb phrases that introduce a research area ("who WORKS ON graph"). Deliberately
# specific — bare "research"/"on" must NOT trigger (e.g. "what research does X involve").
_AREA_TRIGGER = re.compile(
    r"(?:works?\s+on|working\s+on|researches|researching|research\s+(?:in|on)|"
    r"researchers?\s+(?:in|on|of)|studies|studying|specializ(?:es|ing)\s+in|"
    r"expert(?:ise)?\s+in)\s+(.+)")

# ── A15 loose-area (validated) — natural topic→people phrasings the strict trigger misses.
# Two surfaces on the ORG-STRIPPED query; each candidate is VALIDATED against real area tags by
# the caller (never trusted raw). See _extract_area_loose + the loose branch in route().
_LOOSE_PEOPLE = frozenset({"faculty", "professor", "professors", "researcher", "researchers",
                           "academic", "academics"})
# loose-verb / bare-in: a people word … a loose connector … <topic> (determiner KEPT here).
_LOOSE_CONNECTOR = re.compile(
    r"\b(?:faculty|professors?|researchers?|academics?|people|who|anyone)\b.*?"
    r"\b(?:in|doing|study|studies|studying|focus(?:es|ing)?\s+(?:on|in)|"
    r"works?\s+(?:on|in)|working(?:\s+(?:on|in))?|works?)\s+(?P<t>\S.*?)\s*$", re.I)
_DET_LEAD = re.compile(r"^(?:the|a|an)\s+", re.I)
# a bare candidate that is ONLY a generic facet/people-qualifier word is never a real area — it would
# dump on 'research'/'work' or hijack via a word that ALSO appears inside a real tag ("new" in "new
# product development", "international" in "International Finance"). Multi-word topics ("international
# relations faculty") are NOT stopped — only the bare qualifier. (Fable R2, live-DB measured.)
_AREA_FACET_STOP = frozenset({"research", "researches", "research area", "research areas",
                              "work", "works", "study", "studies",
                              "new", "recent", "current", "former", "international", "retired"})

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
# Bare officer-title matcher, compiled for the terse-officer branch (E) + collision detection (D).
_OFFICER_TITLE_RX = re.compile(_OFFICER_TITLE, re.I)
# Tokens allowed to remain after stripping the org phrase + officer-title span from a terse
# officer query ("the gsa president" → residue empty → fire). Any OTHER residue token blocks the
# fire, so "former gsa president" / "gsa president salary" fall through to RAG.
_TERSE_OFFICER_STOP = frozenset({"the", "a", "an", "of", "for", "in", "at", "to",
                                 "who", "is", "are", "and", "s", "'s"})
# F (thread F): tokens allowed to remain after stripping the DISPATCH regex (+ the org phrase, for the
# officer branch) from a bare role/officer fragment. Superset of the terse-officer stop set + person-
# ask words ("who's"/"whos"/"my"/"our") so "who are the officers"/"who is my dean" reduce to empty;
# DELIBERATELY EXCLUDES wh-words what/how/why/when/where so "what is a dean" (definitional) keeps
# residue and falls to RAG rather than deflecting.
_F_STOP = _TERSE_OFFICER_STOP | {"my", "our", "who's", "whos"}

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
               "chief of staff", "executive director", "associate director", "assistant director",
               "provost", "chancellor", "dean", "chair", "director",
               "coordinator", "registrar", "cfo"]
_ROLE_VOCAB_RX = re.compile(
    r"\b(" + "|".join(re.escape(r) for r in sorted(_ROLE_VOCAB, key=len, reverse=True)) + r")s?\b",
    re.I)
_ROLE_SYNONYM = {"cfo": "chief financial officer", "athletic director": "director of athletics"}

# Hierarchical level for org types (everything else defaults to 0 via .get(t, 0)).
ORG_TYPE_LEVEL: dict[str, int] = {
    "university": 3,
    "college": 2,
    "school": 2,
    "department": 1,
}

# Required org-type level for clearly hierarchical roles.
# NOTE: "dean of students" is deliberately EXCLUDED — it is a specific office role
# (not the college-level academic dean), so it stays at level 0 and never climbs.
# Purely administrative titles (director/coordinator/registrar/etc.) also default to 0.
ROLE_SCOPE_LEVEL: dict[str, int] = {
    "provost": 3,
    "vice provost": 3,
    "associate provost": 3,
    "chancellor": 3,
    "dean": 2,
    "associate dean": 2,
    "assistant dean": 2,
    "chair": 1,
    "associate chair": 1,
}


def _climb_to_scope(
    conn: sqlite3.Connection, org_id: int, target_level: int
) -> int | None:
    """Walk parent_id from org_id; return the first org (self included) whose
    ORG_TYPE_LEVEL equals target_level.  Returns None when no matching ancestor
    exists (the caller leaves org_id as-is — graceful degradation).
    Guards against cycles with a 6-hop cap."""
    seen: set[int] = set()
    current: int | None = org_id
    for _ in range(6):
        if current is None or current in seen:
            return None
        seen.add(current)
        row = conn.execute(
            "SELECT type, parent_id FROM organizations WHERE id=?", (current,)
        ).fetchone()
        if row is None:
            return None
        org_type, parent_id = row
        if ORG_TYPE_LEVEL.get(org_type, 0) == target_level:
            return current
        current = parent_id
    return None

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
# Descending-direction words for a metric ranking ("LEAST cited", "FEWEST citations"). Kept SEPARATE
# from _RANK_CUE on purpose — the ascending (Bug A) path depends on _RANK_CUE NOT matching these.
_DESC_DIR = re.compile(r"\b(least|fewest|lowest|bottom)\b")
_TOPN = re.compile(r"\btop\s+(\d+)\b|\b(\d+)\s+most\b")
_PERSON_INTENT = re.compile(
    r"\b(who(?:'s|\s+is|\s+are)|tell\s+me\s+about|info(?:rmation)?\s+on|"
    r"profile\s+of|contact\s+(?:for|info)|reach)\b")
# "who has/have" ("who has the lowest citation in ywcc") — a metric-block-local person cue.
# Deliberately NOT folded into _PERSON_INTENT (reused elsewhere for other routing, e.g. lines
# ~557/629/634/643); scoped to the metric ranking/decline branch only (see person_cue below).
_METRIC_WHO_HAS = re.compile(r"\bwho\s+ha(?:s|ve)\b")
_PERSON_ATTR = re.compile(r"\b(e-?mail|office|phone|number|title|position|bio)\b")
# Looser "more about this person" cues that FOLLOW a bare surname ("koutis info",
# "koutis all info", "koutis details") — distinct from _PERSON_INTENT's "info ON <name>".
_INFO_CUE = re.compile(r"\b(info|information|details|profile|bio|background|everything)\b")
# WS3 person-attribute sub-cues: contact vs title vs the generic card at the person return site.
# 'office' excludes "office hours" (a schedule ask, not a contact field) — review MINOR.
_CONTACT_CUE = re.compile(r"\b(e-?mail|phone|contact|reach)\b|\bnumbers?\b|\boffice\b(?!\s+hours)")
_TITLE_CUE = re.compile(r"\b(title|position)\b|\bwhat\s+does\b.+\bdo\b")
# WS3 org-type enumeration (B3): a PLURAL type noun is the discriminator — 'clubs'/'colleges'/'student
# organizations' fire; SINGULAR 'college'/'club' ("which college is X in", "what college should I apply
# to", "what is the ACM club") do NOT (review BLOCKER: over-match). Departments are NOT enumerated here
# (they stay on the existing _DEPT_ENUM/org_departments branch). Pronoun subjects can't be surname-mined.
_B3_TYPE = re.compile(r"\b(clubs|colleges|student\s+(?:organizations|orgs|groups)|rgos)\b")
_B3_ENUM_VERB = re.compile(r"\b(list|name|show|how\s+many|what|which|any|are\s+there|do\s+we\s+have)\b")
# Personal pronouns (never a surname) always block; this/that only in the flagged shapes
# ("about this", "this one") so "is that Koutis?" isn't over-blocked.
_PRONOUN_SUBJ = re.compile(
    r"\b(his|her|hers|their|theirs|he|she|they|him|them)\b"
    r"|\b(?:about|contact|reach)\s+(?:this|that)\b|\b(?:this|that)\s+one\b")
# Scholar PAPERS: a paper noun (so "most cited PAPER" routes to papers, not the citations metric or a
# professor ranking). Selectors pick the captured slice; default = most-cited.
_PAPER_NOUN = re.compile(r"\b(papers?|publications?|articles?)\b")
_PAPER_NEW = re.compile(r"\b(newest|latest|recent|new)\b")
_THIS_YEAR = re.compile(r"\bthis year\b")
# Scholar TREND: the per-year citation chart. peak ("most-cited YEAR"), a specific year ("in 2019"),
# or growth ("growing / accelerating / trend"). Citation context gates the year branch.
_PEAK_WORD = re.compile(r"\b(most|best|peak|highest|biggest)\b")
_GROWTH_CUE = re.compile(r"\b(grow\w*|accelerat\w*|rising|trend\w*|momentum|trajector\w*)\b")
_YEAR_IN = re.compile(r"\bin\s+((?:19|20)\d{2})\b")
_CITE_CTX = re.compile(r"\bcit(?:e|ed|es|ation|ations)\b")


def _paper_mode(q: str) -> str:
    # "this year" is the most specific slice — check it before the looser newest/latest/recent cue
    # so "latest papers this year" resolves to current_year, not newest.
    if _THIS_YEAR.search(q):
        return "current_year"
    if _PAPER_NEW.search(q):
        return "newest"
    return "most_cited"
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


def _root_org_id(conn: sqlite3.Connection) -> int | None:
    """The university ROOT org id (the active org with no parent) — used to default a bare
    university-wide metric ranking ("most cited professor") to NJIT-wide. None if misconfigured
    (no root), in which case the caller must NOT route (never run with org_id=None)."""
    row = conn.execute(
        "SELECT id FROM organizations WHERE parent_id IS NULL AND is_active=1 LIMIT 1").fetchone()
    return row[0] if row else None


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


# ── D: role-word-office collision (only bare officer-title offices, e.g. slug 'president') ────────
def _bare_officer_office(phrase: str | None) -> str | None:
    """The officer-title word if ``phrase`` is EXACTLY a bare officer title (the office slug case,
    e.g. 'president' → Office of the President). Fullmatch — 'office of the president' is NOT bare,
    so a spelled-out office name is never treated as a role collision."""
    if not phrase:
        return None
    m = _OFFICER_TITLE_RX.fullmatch(phrase.strip())
    return m.group(0).lower() if m else None


def _longest_non_officer_office(conn: sqlite3.Connection, text: str,
                                exclude_id: int) -> tuple[int, str] | None:
    """Longest whole-word org match whose phrase is NOT a bare officer-title office and whose id
    != ``exclude_id`` → (id, phrase), else None. The candidate alternate org in a collision."""
    best: tuple[int, str] | None = None
    for phrase, oid in _org_candidates(conn):
        if oid == exclude_id or not phrase or _bare_officer_office(phrase):
            continue
        if re.search(r"\b" + re.escape(phrase) + r"\b", text):
            if best is None or len(phrase) > len(best[1]):
                best = (oid, phrase)
    return best


def _has_true_officers(conn: sqlite3.Connection, org_id: int) -> bool:
    """True iff the org holds ≥1 active officer/deprep has_role edge (GSA/clubs). The E real-officer
    gate — colleges/depts (admin edges only) fail it, so terse 'officers' can't mislabel them."""
    row = conn.execute(
        "SELECT 1 FROM edges e JOIN nodes o ON o.id=e.dst_id AND o.is_active=1 "
        "WHERE e.type='has_role' AND e.is_active=1 AND e.category IN ('officer','deprep') "
        "AND json_extract(o.attrs,'$.org_id')=? LIMIT 1", (org_id,)).fetchone()
    return row is not None


def _org_answers_title(conn: sqlite3.Connection, org_id: int, title: str) -> bool:
    """True iff the org can actually answer the collision officer ``title`` (D gate 3): a true
    officer/deprep edge, OR an admin edge whose title carries ``title`` as a SEGMENT HEAD (reuse
    people_by_role's matcher; ``^title\\b(?!')`` so 'Vice President'/'President's Advisory' do NOT
    head-match). Confines D's alternate-swap to orgs that genuinely hold the title."""
    if _has_true_officers(conn, org_id):
        return True
    rx = re.compile(r"^" + re.escape(title.lower()) + r"\b(?!')")
    for (eattrs,) in conn.execute(
            "SELECT e.attrs FROM edges e JOIN nodes o ON o.id=e.dst_id AND o.is_active=1 "
            "WHERE e.type='has_role' AND e.is_active=1 AND e.category='admin' "
            "AND json_extract(o.attrs,'$.org_id')=?", (org_id,)):
        titles = (json.loads(eattrs) if eattrs else {}).get("titles") or []
        for t in titles:
            segs = [s.strip() for s in re.split(r",|\s+and\s+", t) if s.strip()]
            if any(rx.match(s.lower()) for s in segs):
                return True
    return False


def _officer_org(conn: sqlite3.Connection, text: str, org_id: int | None,
                 org_phrase: str | None) -> tuple[int | None, str | None]:
    """Org for an OFFICER route (identity/terse), applying the president-collision swap: if the
    resolved org is a bare officer-title office AND a distinct alternate org can actually answer
    that title (gate 3), use the alternate. Otherwise unchanged. `_find_org` itself is untouched,
    so the role/faculty/people branches keep the office as their scope (no regression)."""
    collision = _bare_officer_office(org_phrase)
    if org_id is None or not collision:
        return org_id, org_phrase
    alt = _longest_non_officer_office(conn, text, exclude_id=org_id)
    if alt is not None and _org_answers_title(conn, alt[0], collision):
        return alt
    return org_id, org_phrase


def _is_root_org(conn: sqlite3.Connection, org_id: int) -> bool:
    """True iff ``org_id`` is the university root (no parent). Used by F's officer branch to treat
    "njit officers" (root, no true officers) like an org-less ambiguous ask."""
    row = conn.execute("SELECT parent_id FROM organizations WHERE id=?", (org_id,)).fetchone()
    return row is not None and row[0] is None


# Generic org words shared by many units — must NOT drive a fuzzy match on their own.
_ORG_STOPWORDS = frozenset({
    "college", "school", "department", "dept", "office", "center", "centre", "institute",
    "program", "programs", "of", "the", "and", "njit", "university", "division", "new", "jersey",
    "graduate", "society", "club"})
_FUZZY_ORG_CUTOFF = 90.0        # token_set_ratio floor for a candidate org phrase
_FUZZY_ORG_TOKFLOOR = 88.0      # a query CONTENT token must fuzzily match a candidate token this well


def fuzzy_org(conn: sqlite3.Connection, phrase: str) -> list[tuple[int, float]]:
    """WS2 org head-word / partial resolution: DISTINCT org ids whose name/slug/alias fuzzily matches
    a content head-word ('mechanical' → 'Mechanical & Industrial Engineering'). FALLBACK for when the
    exact whole-word `_find_org` misses. Returns [(org_id, score)] desc, collapsed to distinct orgs.

    Precision guards (WS2 review S3): generic org words (college/school/department…) are dropped so
    they can't match on their own, and a content-token floor stops an incidental `token_set_ratio`
    subset hit from resolving. A broad word ('engineering'/'science') matches MANY orgs → the caller
    sees ≥2 and ABSTAINs (never silently picks one)."""
    q = (phrase or "").strip().lower()
    qtoks = [t for t in re.findall(r"[a-z]+", q) if len(t) > 2 and t not in _ORG_STOPWORDS]
    if not qtoks:
        return []
    scored: dict[int, float] = {}
    for cand_phrase, oid in _org_candidates(conn):
        s = fuzz.token_set_ratio(q, cand_phrase)
        if s < _FUZZY_ORG_CUTOFF:
            continue
        ctoks = [t for t in cand_phrase.split() if len(t) > 2]
        tokmatch = max((fuzz.ratio(qt, ct) for qt in qtoks for ct in ctoks), default=0.0)
        if tokmatch >= _FUZZY_ORG_TOKFLOOR:
            scored[oid] = max(scored.get(oid, 0.0), float(s))
    return sorted(scored.items(), key=lambda kv: -kv[1])


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


def _extract_area_loose(q_for_area: str) -> str | None:
    """A15: surface a candidate research-area topic from a people-noun phrasing the strict
    _AREA_TRIGGER missed. Returns the RAW candidate (NOT validated — the caller checks it against
    real area tags + the fuzzy-org / facet guards). No DB. Two surfaces:
      • topic-first  "<topic> <people-noun>"  (people-noun FINAL, ≤4 tokens, no rank cue) — determiner
        STRIPPED ("the neuroscience faculty" → "neuroscience").
      • loose-verb / bare-in  "faculty in X / doing X / study X"  — determiner KEPT ("faculty in the
        news" → "the news" → won't validate → RAG)."""
    s = re.sub(r"\s+", " ", (q_for_area or "")).strip()
    s = re.sub(r"\s+(?:in|at|of|within)$", "", s).strip()   # dangling prep left by the org-strip
    if not s:
        return None
    toks = s.split()
    # topic-first — people-noun is the LAST token
    if 2 <= len(toks) <= 4 and toks[-1] in _LOOSE_PEOPLE:
        if not _RANK_CUE.search(s) and not _DESC_DIR.search(s):
            cand = _DET_LEAD.sub("", " ".join(toks[:-1])).strip(" .,?")
            if cand:
                return cand
    # loose-verb / bare-in — determiner kept
    m = _LOOSE_CONNECTOR.search(s)
    if m:
        cand = m.group("t").strip(" .,?")
        if cand:
            return cand
    return None


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
    if _PRONOUN_SUBJ.search(stripped):   # "what is his position" / "who do I contact about this" → no KG
        return None
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


def _person_skill(q: str) -> str:
    """Which person-attribute skill a resolved-person query wants: contact vs title vs the full card.
    Contact wins over title if both cue words appear (rare)."""
    if _CONTACT_CUE.search(q):
        return "contact_of_person"
    if _TITLE_CUE.search(q):
        return "title_of_person"
    return "entity_card"


def _with_origin(rt: Route, skill: str, args: dict | None = None) -> Route:
    """A9: tag a person_disambig Route with the skill+args that PRODUCED it, so the resume runs the
    ORIGINALLY-asked question (metric/contact/research/…) instead of a generic bio card. No-op on any
    other Route; `setdefault` so the first (most specific) producer wins and can't be double-tagged."""
    if isinstance(rt, Route) and rt.skill == "person_disambig":
        rt.args.setdefault("origin", {"skill": skill, "args": dict(args or {})})
    return rt


def route(conn: sqlite3.Connection, question: str) -> Route | None:
    q = question.strip().lower().rstrip("?").strip()
    org_id, org_phrase = _find_org(conn, q)
    # D: the org used ONLY by the officer branches — swaps a bare officer-title office (slug
    # 'president') for the real named org when one co-occurs and can answer the title. Every other
    # branch keeps the raw org_id/org_phrase (so registrar/dean-of-students scope is untouched).
    off_id, off_phrase = _officer_org(conn, q, org_id, org_phrase)
    # C1: strip the matched org_phrase from the query before area extraction so org-name
    # tokens (e.g. "studies" in "graduate studies") don't trigger a research-area verb match.
    # Also clean up any trailing preposition left dangling after the strip (e.g. "graph in").
    q_for_area = re.sub(r"\b" + re.escape(org_phrase) + r"\b", " ", q).strip() if org_phrase else q
    area = _extract_area(q_for_area, org_phrase)
    if area:
        area = re.sub(r"\s+(?:in|at|within|of)$", "", area).strip() or None
    named = entity.persons_in_query(conn, q)   # people whose FULL name is in the query

    # ── Scholar PAPERS (a paper noun) — BEFORE area/metric so "most cited paper" can't be mined as a
    # research area or routed as the citations metric / a professor ranking. ────────────────────────
    if _PAPER_NOUN.search(q):
        person = _resolve_person(conn, q, named)
        if isinstance(person, Route):
            return _with_origin(person, "papers_of_person",
                                {"mode": _paper_mode(q), "n": _parse_topn(q)})
        if isinstance(person, dict):
            return Route("papers_of_person",
                         {"entity_id": person["entity_id"], "name": person["name"],
                          "mode": _paper_mode(q), "n": _parse_topn(q)})
        # paper noun but no resolvable person: an org-scoped paper ask ("most cited paper in CS") is
        # cross-person ranking = fast-follow → honest decline (NOT a professor ranking). Bare "most
        # cited paper" (no org, no person) falls through to RAG.
        if org_id is not None:
            return Route("papers_cross_unsupported", {})

    # ── Scholar TREND (per-year chart): peak year / citations-in-year / growth — BEFORE the metric
    # block so "most cited YEAR" isn't caught as the citations metric. Needs a resolvable person. ────
    _peak_cue = bool(_PEAK_WORD.search(q) and "year" in q)
    _year_in = _YEAR_IN.search(q)
    _year_cue = bool(_year_in and _CITE_CTX.search(q) and "since" not in q)
    _growth_cue = bool(_GROWTH_CUE.search(q))
    if _peak_cue or _year_cue or _growth_cue:
        # A9: mode/year depend only on q-cues (in scope above) → compute BEFORE the disambig return
        # so origin.args can carry them (Fable req #3).
        mode = "peak" if _peak_cue else ("growth" if _growth_cue else "year")
        year = int(_year_in.group(1)) if (mode == "year" and _year_in) else None
        person = _resolve_person(conn, q, named)
        if isinstance(person, Route):
            return _with_origin(person, "citation_trend_of_person", {"mode": mode, "year": year})
        if isinstance(person, dict):
            return Route("citation_trend_of_person",
                         {"entity_id": person["entity_id"], "name": person["name"],
                          "mode": mode, "year": year})

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

    # ── A15: VALIDATED loose-area (topic→people phrasings the strict trigger missed) ───────────────
    # Runs only when strict `area` didn't fire. A candidate routes to the KG area skill ONLY IF it is a
    # REAL research-area tag (someone LISTS it), is NOT a facet word, and does NOT fuzzy-match an org
    # (else an org name like "management" would hijack a scope query). Any miss → None → RAG (unchanged).
    if not area:
        _loose = _extract_area_loose(q_for_area)
        if _loose and _loose.lower() not in _AREA_FACET_STOP and not fuzzy_org(conn, _loose):
            if skills.is_listed_research_area(conn, _loose, org_id):
                if "how many" in q or "how much" in q:
                    return Route("count_people_by_research_area", {"area": _loose, "org_id": org_id})
                return Route("people_by_research_area", {"area": _loose, "org_id": org_id})

    # ── metric queries (Scholar citations / h-index / i10) ─────────────────────────
    # Registry-driven (profile_fields.match_metric): only words registered as a Metric alias match,
    # so non-metric uses ("how do I cite a paper", "form i10") don't. Placed AFTER the area branches
    # (so "most cited research area" stays an area question) and BEFORE the generic person branches.
    # Ranking needs an org + a rank cue; single-person needs a resolvable person. A metric word with
    # NEITHER must FALL THROUGH (no return here) so the normal person/RAG branches still run.
    mm = profile_fields.match_metric(q)
    if mm is not None:
        field_key, metric = mm
        # A person/faculty cue gates the ranking branches (both descending decline and the no-org
        # default), so a metric alias on a NON-people question ("most cited PAPER", "fewest citations
        # needed to graduate") falls through to RAG instead of a people ranking/decline.
        person_cue = bool(_FACULTY_CUE.search(q) or _PERSON_INTENT.search(q)
                          or _METRIC_WHO_HAS.search(q))
        # Bug B (position-1): a descending-direction ranking of people is unsupported (Scholar coverage
        # is partial; "least cited" over a partial set is misleading + unkind). Decline deterministically
        # — never RAG, never a roster dump — so no person is ever named as "least <metric>".
        if person_cue and _DESC_DIR.search(q):
            # Thread the org so the follow-up resume ("most instead") can scope top_people_by_metric.
            # No org named but a person cue → default to the NJIT root (university-wide), mirroring the
            # ascending branch below.
            desc_org, defaulted = org_id, False
            if desc_org is None:
                root = _root_org_id(conn)
                if root is not None:
                    desc_org, defaulted = root, True
                # else: no root org -> desc_org stays None, defaulted stays False; the decline still
                # fires (a decline needs no org), the resume just won't be scoped (consumer guards
                # org_id=None). Never emit org_defaulted=True with org_id=None (contradictory).
            return Route("metric_descending_unsupported",
                         {"field_key": field_key, "metric_key": metric.key,
                          "org_id": desc_org, "n": _parse_topn(q), "org_defaulted": defaulted})
        if _RANK_CUE.search(q):
            if org_id is not None:
                return Route("top_people_by_metric",
                             {"org_id": org_id, "field_key": field_key,
                              "metric_key": metric.key, "n": _parse_topn(q)})
            # Bug A: no org named, but a person/faculty cue → default to the NJIT root (university-wide;
            # "most cited professor" means NJIT-wide). org_defaulted flags the answer to invite narrowing.
            if person_cue:
                root = _root_org_id(conn)
                if root is not None:
                    return Route("top_people_by_metric",
                                 {"org_id": root, "field_key": field_key,
                                  "metric_key": metric.key, "n": _parse_topn(q),
                                  "org_defaulted": True})
        person = _resolve_person(conn, q, named)
        if isinstance(person, Route):
            return _with_origin(person, "metric_of_person",
                                {"field_key": field_key, "metric_key": metric.key})
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
            return _with_origin(person, "link_of_person", {"field_key": field_key})
        if isinstance(person, dict):
            return Route("link_of_person",
                         {"entity_id": person["entity_id"], "name": person["name"],
                          "field_key": field_key})

    if (off_id is not None and _OFFICER_IDENTITY.search(q)
            and not _OFFICER_PROCESS.search(q)):
        return Route("officers_in_org", {"org_id": off_id})

    # ── role lookup: find a person BY THEIR ROLE ("who is the provost", "the chair of cs",
    # "who are the deans"). Comes BEFORE the department/faculty/people branches so a NAMED role
    # wins over "list <org>'s departments" (e.g. "chair of cs department" is a role ask, not a
    # department list). Officer titles stay with the officer branch above; process/eligibility
    # shapes ("how to become a dean") fall through to RAG. Empty result → RAG.
    if not _LEADERSHIP_PROCESS.search(q):
        rm = _ROLE_VOCAB_RX.search(q)
        if rm:
            role_word = rm.group(1).lower()
            explicit = bool(_PERSON_INTENT.search(q) or _ENUM_TRIGGER.search(q)
                            or _ROLE_OF_ORG.search(q))
            # The bare org_id fallback over-triggers when the role word merely NAMES the org
            # (e.g. "registrar office hours": 'registrar' both resolves the office AND matches
            # _ROLE_VOCAB, but the question is about the office, not a person). In that overlap,
            # require an explicit person/enum/role-of-org cue; otherwise the bare org match suffices.
            role_is_org = bool(org_phrase and role_word in org_phrase.lower())
            if explicit or (org_id is not None and not role_is_org):
                role = _ROLE_SYNONYM.get(role_word, role_word)
                # Hierarchy climb: if the role's required org-type level outranks the
                # resolved org's type, walk up to the nearest ancestor that matches.
                # Example: "dean of CS dept" → dean needs college-level → climb to YWCC.
                role_level = ROLE_SCOPE_LEVEL.get(role, 0)
                target_org = org_id
                if org_id is not None and role_level > 0:
                    org_type_row = conn.execute(
                        "SELECT type FROM organizations WHERE id=?", (org_id,)
                    ).fetchone()
                    cur_level = ORG_TYPE_LEVEL.get(org_type_row[0], 0) if org_type_row else 0
                    if role_level > cur_level:
                        climbed = _climb_to_scope(conn, org_id, role_level)
                        if climbed is not None:
                            target_org = climbed
                return Route("people_by_role", {"role_head": role, "org_id": target_org})

    # ── terse OFFICER forms (E): "<org> officers" / "<org> president" / "<org> treasurer" with NO
    # verb (the verb-ful case is the officer-identity branch above). Placed AFTER the role branch so
    # a role word wins ("cs chair secretary" → people_by_role). Gated to orgs holding a TRUE
    # officer/deprep role, so a college's "officers" can't mislabel its deans. Two guards keep
    # process/attribute/tense forms out: title-is-org (the title merely NAMES the office, e.g.
    # "president office hours") and zero-residue (any non-stopword left after stripping org+title).
    if off_id is not None:
        otm = _OFFICER_TITLE_RX.search(q)
        if otm and not _OFFICER_PROCESS.search(q):
            title_word = otm.group(0).lower()
            title_is_org = bool(off_phrase and title_word in off_phrase.lower())
            residue = re.sub(r"\b" + re.escape(off_phrase) + r"\b", " ", q) if off_phrase else q
            residue = _OFFICER_TITLE_RX.sub(" ", residue)
            leftover = [t for t in re.findall(r"[a-z0-9'\-]+", residue)
                        if t not in _TERSE_OFFICER_STOP]
            if not title_is_org and not leftover and _has_true_officers(conn, off_id):
                return Route("officers_in_org", {"org_id": off_id})

    # ── org enumeration by TYPE (WS3 B3): clubs / colleges only. Fires ONLY on an enumerate verb + a
    # PLURAL type noun; SINGULAR "which college is X in" falls through to RAG. Parent scopes ONLY to a
    # NON-root org (blocker: "list colleges at NJIT" must NOT scope to the university root → None).
    tm = _B3_TYPE.search(q)
    if tm and _B3_ENUM_VERB.search(q):
        org_type = "college" if tm.group(1).startswith("college") else "club"
        parent = org_id if (org_id is not None and not _is_university_root(conn, org_id)) else None
        return Route("orgs_by_type", {"org_type": org_type, "parent_org_id": parent})

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
            return _with_origin(person, "research_of_person", {})
        if isinstance(person, dict):
            return Route("research_of_person",
                         {"entity_id": person["entity_id"], "name": person["name"]})

    # entity card: a specific named person (who-is / tell-me-about / "<name>'s email" /
    # bare name). LAST + most-guarded so it never hijacks a "who works on X" ask.
    qn = _NAME_PREFIX.sub("", q).strip()
    if len(named) == 1 and (_PERSON_INTENT.search(q) or _PERSON_ATTR.search(q)
                            or _CONTACT_CUE.search(q) or _TITLE_CUE.search(q)
                            or _is_bare_name(qn, named[0])):
        return Route(_person_skill(q),
                     {"entity_id": named[0]["entity_id"], "name": named[0]["name"]})
    if len(named) > 1 and (_PERSON_INTENT.search(q) or _PERSON_ATTR.search(q)
                           or _CONTACT_CUE.search(q) or _TITLE_CUE.search(q)):
        return _with_origin(Route("person_disambig", {"candidates": named}), _person_skill(q), {})

    # surname-only: "professor Wang" / "who is Wang" / "Koutis's email" / "koutis info" /
    # bare "koutis". The trigger is person-directed (intent / attribute / title prefix / an
    # info cue) OR the whole query is just one token (a lone surname). Resolution is by real
    # last name, so a non-person single word ("events") simply finds nothing and falls to RAG.
    qn_toks = _qtokens(qn)
    if (_PERSON_INTENT.search(q) or _PERSON_ATTR.search(q) or _CONTACT_CUE.search(q)
            or _TITLE_CUE.search(q) or _NAME_PREFIX.search(q) or _INFO_CUE.search(q)
            or len(qn_toks) == 1):
        person = _resolve_surname(conn, q)
        if isinstance(person, Route):
            return _with_origin(person, _person_skill(q), {})
        if isinstance(person, dict):
            return Route(_person_skill(q),
                         {"entity_id": person["entity_id"], "name": person["name"]})

    # ── F: genuinely-ambiguous bare role/officer fragment → abstain-hint. TERMINAL, runs LAST so
    # every confident branch above already declined (that IS the confidence check). Two-way dispatch
    # by which vocab matched; the org test is branch-ASYMMETRIC — role: strict org-less; officer:
    # org-less OR root-with-no-true-officers (the "njit officers" AFROTC-sibling fix). Strip ONLY the
    # dispatch regex so a both-tokens query ("director secretary") self-blocks on residue.
    if not _OFFICER_PROCESS.search(q):
        role_m = _ROLE_VOCAB_RX.search(q)
        if role_m and org_id is None:                       # branch 1 — role vocab, strictly org-less
            residue = _ROLE_VOCAB_RX.sub(" ", q)
            if not [t for t in re.findall(r"[a-z0-9'\-]+", residue) if t not in _F_STOP]:
                w = role_m.group(1).lower()
                return Route("people_by_role",
                             {"role_head": _ROLE_SYNONYM.get(w, w), "org_id": None})
        off_m = _OFFICER_TITLE_RX.search(q)
        if off_m and (org_id is None                        # branch 2 — officer title, org-less OR
                      or (_is_root_org(conn, org_id)         #   root-with-no-true-officers
                          and not _has_true_officers(conn, org_id))):
            residue = re.sub(r"\b" + re.escape(org_phrase) + r"\b", " ", q) if org_phrase else q
            residue = _OFFICER_TITLE_RX.sub(" ", residue)
            if not [t for t in re.findall(r"[a-z0-9'\-]+", residue) if t not in _F_STOP]:
                return Route("ambiguous_officers", {})
    return None
