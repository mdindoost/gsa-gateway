"""Deterministic query correction (C+A): acronym dictionary + router-leader-rule support.
Spec §14. No LLM. Gated by QUERY_CORRECT_ENABLED (read at call time)."""
from __future__ import annotations
import os
import re


# Curated, reviewed, GSA-equal. Whole-word. The LLM is forbidden acronyms (spec §14.1).
# HARD RULE — the dictionary MUST NOT expand a token the router already resolves as an
# ORG identifier (slug / alias). Expanding a resolvable org slug into its full name
# (e.g. "gsa" -> "gsa graduate student association") breaks the router's native org
# resolution and DEMOTES a correct structured route (officers_in_org / faculty_in_department)
# into RAG. So `gsa`, `cs`, `ece` are deliberately EXCLUDED (router resolves them natively);
# this dictionary carries ONLY generic vocabulary normalizers for tokens the router can't
# resolve on its own. (Caught by the $0 route-diff gate: expanding `gsa` broke 7 GSA queries.)
ACRONYMS: dict[str, str] = {
    "dept": "department", "dep": "department",
    "prof": "professor",
    "sci": "science",
    "eng": "engineering",
    "uni": "university",
}
_ACRONYM_RX = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, ACRONYMS), key=len, reverse=True)) + r")\b", re.I)


def enabled() -> bool:
    return os.getenv("QUERY_CORRECT_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def augment_acronyms(text: str, protected: set[str] | None = None) -> str:
    protected = protected or set()
    def _sub(m: re.Match) -> str:
        tok = m.group(1)
        if tok.lower() in protected:
            return tok
        return f"{tok} {ACRONYMS[tok.lower()]}"
    return _ACRONYM_RX.sub(_sub, text)
