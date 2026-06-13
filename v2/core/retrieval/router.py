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


@dataclass
class Route:
    skill: str
    args: dict


def _org_candidates(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    cands: list[tuple[str, int]] = []
    for oid, name, slug in conn.execute(
            "SELECT id,name,slug FROM organizations WHERE is_active=1"):
        cands.append((name.lower(), oid))
        cands.append((slug.lower(), oid))
        if "-" in slug:
            cands.append((slug.replace("-", " ").lower(), oid))
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

    if "how many" in q and area:
        return Route("count_people_by_research_area", {"area": area, "org_id": org_id})
    if area:
        return Route("people_by_research_area", {"area": area, "org_id": org_id})
    if "department" in q and org_id is not None and "faculty" not in q and "professor" not in q:
        return Route("org_departments", {"org_id": org_id})
    if ("faculty" in q or "professor" in q) and org_id is not None:
        return Route("faculty_in_department", {"org_id": org_id})
    return None
