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


# Per-office intent DESCRIPTION (what the office handles, in student terms) — embedded once
# and matched against the query by meaning. Richer + more general than substring cues; this is
# the structural signal (the keyword INTENT_MAP above is a high-precision fallback only).
OFFICE_DESCRIPTIONS: dict[str, str] = {
    "registrar": "registration, enrolling in or dropping classes, registration and advising "
                 "holds that block sign-up, transcripts, enrollment verification, the academic calendar",
    "bursar": "tuition bills, paying what you owe, payment plans, refunds, your student account "
              "balance, billing holds",
    "financialaid": "financial aid, FAFSA, scholarships, grants, student loans, paying for school",
    "ogi": "international students, F-1 and J-1 visas, the I-20, OPT and CPT work authorization, "
           "SEVIS, immigration status, studying abroad",
    "graduate-admissions": "applying for admission, application status, admission requirements, "
                           "getting admitted to a graduate program",
    "graduate-studies": "thesis and dissertation review, graduate degree requirements, the "
                        "defense process, graduate academic policy",
    "dean-of-students": "student conduct, code-of-conduct violations, grievances and complaints, "
                        "student support",
    "counseling": "mental health, counseling, emotional crisis, anxiety, stress, wellness support",
    "career-development": "careers, resumes, the job search, finding internships, career fairs, "
                          "interviewing",
    "oars": "disability accommodations, accessibility services, extra exam time, accommodation letters",
    "ist": "IT help, resetting your password, wifi, NJIT email, Canvas, the service desk, "
           "technology support",
}


class SemanticOfficeClassifier:
    """Meaning-based query -> office. Embeds each office description once, then returns the
    nearest office for a query if its cosine similarity clears `threshold` (else None, so a
    non-procedural query doesn't mis-fire). A SIGNAL for a bounded prior, not a hard scope."""

    def __init__(self, embedder, threshold: float = 0.60):
        self.embedder = embedder
        self.threshold = threshold
        self._slugs = list(OFFICE_DESCRIPTIONS)
        self._mat = [embedder.embed_document(OFFICE_DESCRIPTIONS[s]) for s in self._slugs]

    def classify(self, query: str):
        qv = self.embedder.embed_query(query)
        if not qv:
            return None, 0.0
        best_s, best_sim = None, -1.0
        for slug, dv in zip(self._slugs, self._mat):
            if not dv:
                continue
            sim = sum(a * b for a, b in zip(qv, dv))  # both L2-normalized -> cosine
            if sim > best_sim:
                best_s, best_sim = slug, sim
        return (best_s, best_sim) if best_sim >= self.threshold else (None, best_sim)


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
