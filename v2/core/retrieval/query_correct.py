"""Deterministic query correction (C+A): acronym dictionary + router-leader-rule support.
Spec §14. No LLM. Gated by QUERY_CORRECT_ENABLED (read at call time)."""
from __future__ import annotations
import os
import re


# Curated, reviewed, GSA-equal. Whole-word. The LLM is forbidden acronyms (spec §14.1).
ACRONYMS: dict[str, str] = {
    "gsa": "graduate student association",
    "dept": "department", "dep": "department",
    "prof": "professor",
    "cs": "computer science",
    "sci": "science",
    "eng": "engineering",
    "ece": "electrical and computer engineering",
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
