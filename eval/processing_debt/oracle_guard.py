from __future__ import annotations
import urllib.request, re
from eval.processing_debt.types import OracleAnswer, Nugget, GuardVerdict

UA = "GSA-Gateway-Research/1.0"
_INTERNAL_HINTS = ("gsa", "graduate student association", "officer", "president of the gsa", "rgo", "club")

def _default_fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", "replace")
    return re.sub(r"<[^>]+>", " ", html)   # crude tag strip; sufficient for entailment span

def _default_is_internal(fact: str) -> bool:
    f = fact.lower()
    return any(h in f for h in _INTERNAL_HINTS)

def guard(nugget: Nugget, oracle: OracleAnswer, *, fetch=None, entails_fn=None, is_internal=None) -> GuardVerdict:
    # LENIENT + WINDOWED support check: keep unless the citation is CLEARLY unrelated (Fable). A strict/
    # unwindowed check dropped real loosely-cited facts (e.g. "Vincent Oria is the Chair") and broke SC2.
    from eval.processing_debt.entailment import supported_by as _entails
    fetch = fetch or _default_fetch
    entails_fn = entails_fn or _entails
    is_internal = is_internal or _default_is_internal
    if is_internal(nugget.text):
        return GuardVerdict("we_are_authority")
    for cite in oracle.citations:
        if cite.snippet and entails_fn(nugget.text, cite.snippet):
            return GuardVerdict("supported", cited_url=cite.url, evidence_span=cite.snippet[:300])
        try:
            page = fetch(cite.url)
        except Exception:
            continue
        if page and entails_fn(nugget.text, page):   # window the FULL page (supported_by bounds the work);
            return GuardVerdict("supported", cited_url=cite.url, evidence_span=page[:300])  # [:8000] cut off
            # supporting text past char 8000 (e.g. a chairperson signature at ~11k) and wrongly DROPPED it.
    return GuardVerdict("unsupported")
