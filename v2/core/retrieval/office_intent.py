"""Procedural-intent -> office resolver (data, not a hidden keyword hack).

Many transactional questions name NO org ("who handles a registration hold") so the
router's name/slug/alias resolution can't scope them. This module maps procedural intent
to the office that owns it. INTENT_MAP is explicit, VERSIONED DATA meant to be grown — and
it is only a SIGNAL: the caller feeds it as a bounded, pool-only prior (never a hard
override), and a HELD-OUT office-intent eval is the honest judge of whether it generalizes.
If the held-out set shows it doesn't, the structural answer is a learned classifier — this
module is the deterministic first cut, deliberately kept separate from how it's consumed.

`resolve_office_slug` returns the office slug for a query, or None. `resolve_office_org_id`
maps that to a live org id (None if the office isn't in this DB).
"""
from __future__ import annotations

import re
import sqlite3
from typing import Optional

# office slug -> procedural cue phrases (lowercase, substring-matched on the query)
INTENT_MAP: dict[str, list[str]] = {
    "registrar": ["registration hold", "register for class", "drop a class", "add a class",
                  "transcript", "enrollment verification", "withdraw from a course",
                  "academic calendar", "registrar", "course registration"],
    "bursar": ["tuition", "my bill", "make a payment", "refund", "student account",
               "account balance", "bursar", "payment plan"],
    "financialaid": ["financial aid", "fafsa", "scholarship", "student loan", "grant money",
                     "sfas", "aid package"],
    "ogi": ["i-20", "opt", "cpt", "visa", "sevis", "international student", "study abroad",
            "immigration status", "global initiatives"],
    "graduate-admissions": ["how do i apply", "application status", "admission requirement",
                            "get admitted", "admissions office"],
    "dean-of-students": ["student conduct", "code of conduct", "file a grievance",
                         "dean of students", "student complaint"],
    "counseling": ["counseling", "mental health", "c-caps", "talk to a therapist", "wellness"],
    "career-development": ["resume help", "find an internship", "job search", "career services",
                           "career fair", "career development"],
    "oars": ["accommodation", "accessibility", "disability service", "oars"],
    "ist": ["reset my password", "wifi", "campus email", "technology support", "it help desk"],
}


def resolve_office_slug(query: str) -> Optional[str]:
    """Return the office slug whose procedural cue appears in `query`, else None.

    On a multi-office match the LONGEST matched cue wins (more specific intent).
    """
    q = query.lower()
    best_slug, best_len = None, 0
    for slug, cues in INTENT_MAP.items():
        for cue in cues:
            if cue in q and len(cue) > best_len:
                best_slug, best_len = slug, len(cue)
    return best_slug


def resolve_office_org_id(query: str, conn: sqlite3.Connection) -> Optional[int]:
    slug = resolve_office_slug(query)
    if slug is None:
        return None
    row = conn.execute(
        "SELECT id FROM organizations WHERE slug = ? AND type = 'office' AND is_active = 1",
        (slug,),
    ).fetchone()
    return row[0] if row else None
