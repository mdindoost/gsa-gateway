"""Explicit 'search njit for X' detector — a deterministic live-search trigger.

The user literally asked us to go to the live njit.edu site, so it is safe to run a live
search directly (this is NOT free-text NLU — it only matches explicit search phrasings).
Returns the extracted topic X, or None when the message isn't such a request.
"""
from __future__ import annotations

import re
from typing import Optional

# Shown when a USER-triggered live search comes back empty (explicit search / offer tap).
# One shared constant so the handler and the connectors can't drift.
LIVE_NOT_FOUND_MSG = (
    "I searched NJIT's website but couldn't find anything on that. "
    "Try rephrasing, or contact a GSA officer at gsa-vpa@njit.edu."
)

_PATTERNS: tuple[re.Pattern, ...] = (
    # "search [the] njit[.edu] [website|site] for X"
    re.compile(r"\bsearch\s+(?:the\s+)?njit(?:\.edu)?(?:\s+(?:website|site))?\s+for\s+(.+)", re.IGNORECASE),
    # "check [the] njit[.edu]['s site] for X"
    re.compile(r"\bcheck\s+(?:the\s+)?njit(?:\.edu)?(?:'s\s+site)?\s+for\s+(.+)", re.IGNORECASE),
    # "look up X on njit[.edu]"
    re.compile(r"\blook\s+up\s+(.+?)\s+on\s+njit(?:\.edu)?\b", re.IGNORECASE),
)


def parse_explicit_live_search(text: str) -> Optional[str]:
    if not text:
        return None
    for pat in _PATTERNS:
        m = pat.search(text)
        if m:
            topic = m.group(1).strip().rstrip("?!.").strip()
            return topic or None
    return None
