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

from v2.core.retrieval import skills

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
# "who LISTS X as a research area" -> precise tag match.
_LISTS_AREA = re.compile(r"who\s+lists?\s+(.+?)\s+as\s+(?:an?\s+)?research\s+area")

# Officer / governance cue ("who are the OFFICERS", "who is the PRESIDENT / VP finance").
# Deliberately excludes 'professor'/'faculty' so it never hijacks the YWCC faculty branch.
_OFFICER = re.compile(
    r"\b(officers?|e-?board|executive board|president|vice[- ]president|\bvp\b|"
    r"treasurer|secretary|deprep|department representatives?)\b")

# "who works at/in <org>", "people in <org>", "<org> staff/team" -> the full roster.
_PEOPLE = re.compile(
    r"\b(who works?\b|works? (?:at|in|for)\b|people (?:in|at|of)\b|"
    r"staff (?:of|at|in)\b|team (?:of|at|in)\b|members? of\b)")


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


def route(conn: sqlite3.Connection, question: str) -> Route | None:
    q = question.strip().lower().rstrip("?").strip()
    org_id, org_phrase = _find_org(conn, q)
    area = _extract_area(q, org_phrase)

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
        # a 'faculty'/'professor' mention WITHOUT a ranking cue is a roster ask
        # ("list faculty and their areas"), not area enumeration — fall through to the
        # faculty branch instead of answering with a bare list of area names.
        if "faculty" not in q and "professor" not in q:
            return Route("areas_in_org", {"org_id": org_id})

    if org_id is not None and _OFFICER.search(q):
        return Route("officers_in_org", {"org_id": org_id})

    if org_id is not None and _PEOPLE.search(q):
        return Route("people_in_org", {"org_id": org_id})

    if "department" in q and org_id is not None and "faculty" not in q and "professor" not in q:
        return Route("org_departments", {"org_id": org_id})
    if ("faculty" in q or "professor" in q) and org_id is not None:
        return Route("faculty_in_department", {"org_id": org_id})
    return None
