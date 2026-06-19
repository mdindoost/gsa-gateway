"""Stakes classification + volatile redaction for the NJIT grad-content crawl (task #5).

Senior-reviewed safety policy. Three handling buckets for crawled content:
  • high     — a high-stakes RULE/consequence (immigration, forfeiture, dismissal, test-score
               requirement) or a financial/immigration tree doc that carried values. Ingested
               STAGED (is_active=0) for human sign-off — invisible to students until approved.
  • volatile — a specific value that changes (tuition $, fee %, a deadline date). The value is
               REDACTED and replaced with a pointer to the live page, so the bot never asserts
               a stale number. Deterministic + content-free: NO LLM rewrite, NO value carried.
  • low      — everything else → ingested live.

URL-tree signal (most reliable) + keyword rules; unknown in a high tree defaults to high.
"""
from __future__ import annotations

import re

# Immigration / financial trees → high-stakes by default (wrong answers harm students).
_HIGH_TREES = ("/bursar", "/financialaid", "/student-accounts", "/global",
               "/international", "/oie", "/oge", "/sponsored")

# A high-stakes RULE / consequence anywhere in the text → stage for sign-off.
# Short exact terms (opt/cpt/gre…) keep word boundaries so they don't match inside
# "option"/"agree"; stems get a \w* tail so "forfeit" matches "forfeits"/"forfeiture".
_HIGH_RULE = re.compile(
    r"\b(?:visas?|i-?20|sevis|cpt|opt|gre|toefl|ielts)\b"
    r"|\b(?:immigration|work\s+authoriz|out\s+of\s+status|maintain(?:ing)?\s+status|"
    r"full[- ]time\s+requirement|forfeit|dismiss|terminat|expel|expuls|probation|penalt|"
    r"revok|deport|score\s+requirement)\w*", re.I)

# Volatile VALUES (redacted + pointed to the live page; never asserted).
_MONEY   = re.compile(r"(\$\s?\d[\d,]*(?:\.\d+)?|\bUSD\s?\d[\d,]*)", re.I)
_PERCENT = re.compile(r"\b\d{1,3}(?:\.\d+)?\s?%")
_MONTHS  = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE    = re.compile(rf"\b{_MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?\b|\b\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?\b", re.I)
_DEADLINE_CTX = re.compile(r"\b(deadline|due|by|last\s+day|no\s+later|before|on\s+or\s+after)\b", re.I)


def in_high_tree(url: str) -> bool:
    u = (url or "").lower()
    return any(t in u for t in _HIGH_TREES)


def redact_volatile(text: str, source_url: str) -> tuple[str, int]:
    """Replace each line carrying a volatile value (money, percent, or a deadline date) with a
    content-free pointer to the live page. Returns (clean_text, n_redacted). Deterministic and
    value-free — the extracted number/date is DROPPED, never re-emitted."""
    pointer = f"(For the current figure, see the live page: {source_url})"
    out: list[str] = []
    n = 0
    for line in (text or "").splitlines():
        volatile = bool(
            _MONEY.search(line) or _PERCENT.search(line)
            or (_DATE.search(line) and _DEADLINE_CTX.search(line)))
        if volatile:
            out.append(pointer)
            n += 1
        else:
            out.append(line)
    return "\n".join(out), n


def has_unredacted_value(text: str) -> bool:
    """Tripwire: True if a money or percent value survived redaction. The ingest MUST fail if
    this is true for a doc that went through redact_volatile — a leaked stale value is exactly
    the hallucination class the verbatim pipeline forbids."""
    return bool(_MONEY.search(text or "") or _PERCENT.search(text or ""))


def classify_doc(url: str, text: str, had_volatile: bool = False) -> str:
    """Decide the staging bucket for a (redacted) doc: 'high' (stage for sign-off) or 'low'
    (live). ``had_volatile`` = the doc carried volatile values before redaction.

    high if: a high-stakes RULE is present, OR it's a financial/immigration-tree doc that
    carried values (verify the surrounding rules around the redacted figures). Otherwise low —
    e.g. a Bursar 'office hours / location' page with no rules and no values goes live."""
    if _HIGH_RULE.search(text or ""):
        return "high"
    if in_high_tree(url) and had_volatile:
        return "high"
    return "low"
