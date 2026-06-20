"""Deflection detector — the OFFER-only live-search signal.

Used by `_rag_pipeline` to decide whether to attach a "want me to search NJIT's website?"
offer to an answer that READS as answered but punts the user elsewhere FOR the answer (the
silent confident-deflection hole, e.g. "for current hours, see library.njit.edu"). This
NEVER auto-fires anything — a true positive surfaces a button, a false positive is a (cheap,
not free) extra button. So we anchor to the deflection *gesture*, not to any mention of a
contact: honest-partial / heads-up answers that route the user to an office on purpose are
the project's CORRECT behavior and must NOT match (no "contact"/"reach out" tells).

Tag-at-source is the PRIMARY detector (the caller flags its own canned no-info text); this
prose matcher is the NARROW secondary net for LLM prose. Match on the PRE-heads-up text — the
heads-up line itself says "confirm with <office>" and would otherwise self-trigger.
"""
from __future__ import annotations

import re

# Each "see/visit/check" tell requires a volatile-info qualifier (current/latest/exact/more…)
# nearby OR an explicit *.njit.edu target — that distinguishes "for current hours, see
# library.njit.edu" (deflection) from "see his website" / "see the syllabus" in a bio.
_VOLATILE = r"current|latest|up[- ]?to[- ]?date|most recent|exact|specific|detailed|more|further"

DEFLECTION_TELLS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # "for the {current/latest/exact/more} …, see/visit/check/refer to/consult …"
        rf"for (?:the )?(?:{_VOLATILE})\b[^.]*?\b(?:see|visit|check|refer to|go to|consult)\b",
        # "…(please) see/visit/check … X.njit.edu"
        r"\b(?:please )?(?:see|visit|check|refer to|consult)\b[^.]*?\b\S+\.njit\.edu",
        # "you can/should check the website/page/site for the current/latest/… …"
        rf"\b(?:you (?:can|should|may)|i(?:'d| would) (?:recommend|suggest)) "
        rf"(?:check|visit|see|look)\w*\b[^.]*?\b(?:website|page|site)\b for (?:the )?(?:{_VOLATILE})\b",
        # explicit no-info admissions (belt-and-suspenders vs tag-at-source)
        r"\bi (?:don't|do not|wasn't able to|was not able to|couldn't|could not) (?:have|find|locate)\b",
        r"\bi (?:don't|do not) have (?:that|specific|detailed|the exact|enough) (?:information|details|data)\b",
        r"\bnot (?:available|listed|specified|included) in (?:the|our|my|gsa'?s?) "
        r"(?:knowledge base|kb|records|data)\b",
    )
)


def looks_like_deflection(text: str) -> bool:
    """True if the answer punts the user elsewhere FOR the answer (a deflection gesture)."""
    if not text:
        return False
    return any(pat.search(text) for pat in DEFLECTION_TELLS)
