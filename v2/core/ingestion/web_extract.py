"""Phase 1b — grounded fact extraction from a crawled personal-site page.

The LLM reads a page and returns facts about the professor, each with a VERBATIM
evidence quote. We then **programmatically verify the quote is literally present
on the page** and discard any fact that isn't — so a hallucinated or paraphrased
fact (the failure mode of LLM extraction) cannot survive. This span-grounding is
the trust core; it makes a local model safe to use.

Long pages are CHUNKED into overlapping windows (decompose, don't truncate — no
data is dropped); facts from every window are grounded against the FULL page text
and de-duplicated.

Prompt assembly, JSON parsing, grounding and chunking are pure and unit-tested;
the LLM call is injected (``extract_page(..., call_llm)``).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

# Closed set of fields we accept (maps later to knowledge_items types). Anything
# else the model emits is dropped.
ALLOWED_FIELDS = {
    "bio", "research_area", "award", "experience", "publication",
    "software", "project", "group", "service",
}
# Department-agnostic cleanup (works for CS, DS, any dept):
# research areas that are just a department / generic label carry no signal.
_GENERIC_AREA = {
    "computer science", "data science", "informatics", "computing", "research",
    "science", "engineering", "technology", "mathematics", "statistics",
}
# committee/admin language -> this is service, whatever the model labelled it.
_SERVICE_RE = re.compile(
    r"\b(committee|senate|coordinator|editorial|editor|program chair|reviewer|"
    r"organizing|review panel|advisory board)\b", re.I)
_STOP = {"the", "and", "for", "with", "from", "that", "this", "their", "his", "her",
         "are", "was", "were", "has", "have", "department", "university", "njit"}
MIN_BIO_CHARS = 40
# Speculative fields the model loves to mislabel (a thesis number tagged "group",
# a paper title tagged "software"). Require the evidence to actually mention the
# concept — a department-agnostic guard against mislabelling.
_FIELD_KEYWORDS = {
    "software": ("software", "tool", "library", "package", "framework", "toolkit",
                 "code", "system", "implementation", "benchmark", "dataset"),
    "group": ("group", "lab", "laboratory", "team", "center", "centre", "consortium"),
    "project": ("project", "grant", "funded", "funding", "award no", "nsf"),
}
# award values too generic to be useful on their own.
_GENERIC_AWARD = {"best paper award", "best paper", "award", "paper award",
                  "best paper award (back end)"}


def _content_words(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) >= 4 and w not in _STOP]


def _value_supported(value: str, evidence: str) -> bool:
    """The value's significant words should mostly appear in its evidence quote — so
    a value can't be an LLM inference loosely attached to a tangential quote (e.g. a
    'research_area' justified by an unrelated publication citation)."""
    words = _content_words(value)
    if not words:
        return True
    ev = _norm(evidence)
    hits = sum(1 for w in words if w in ev)
    return hits / len(words) >= 0.6


def _refine(f: "Fact") -> "Fact | None":
    """Department-agnostic quality pass over a grounded fact. Returns the (possibly
    relabelled) fact, or None to drop it."""
    field, value = f.field, f.value.strip()
    if len(value) < 4 or not _value_supported(value, f.evidence):
        return None
    if _SERVICE_RE.search(value) or _SERVICE_RE.search(f.evidence):
        if field in ("group", "experience"):
            field = "service"
    if field == "research_area" and _norm(value) in _GENERIC_AREA:
        return None
    if field == "award" and _norm(value) in _GENERIC_AWARD:
        return None
    if field == "bio" and len(value) < MIN_BIO_CHARS:
        return None
    if field in _FIELD_KEYWORDS:
        hay = f"{value} {f.evidence}".lower()
        if not any(k in hay for k in _FIELD_KEYWORDS[field]):   # field not lexically supported
            return None
    return Fact(field=field, value=value, evidence=f.evidence, source_url=f.source_url)
WINDOW = 10_000   # chars per extraction window
OVERLAP = 500     # window overlap so a fact split across a boundary still appears whole
MIN_EVIDENCE = 12  # an evidence quote shorter than this can't meaningfully ground a fact

SYSTEM = (
    "You extract structured facts about ONE university professor from the text of a "
    "web page they own. Return ONLY a flat JSON ARRAY (a list) of objects — one object "
    "PER FACT — like this:\n"
    '[{"field":"award","value":"NSF CAREER Award (2012)","evidence":"2012 NSF CAREER award"},'
    '{"field":"software","value":"GraphTool","evidence":"GraphTool, an open-source library"}]\n'
    "Each object is {\"field\": F, \"value\": V, \"evidence\": E} where:\n"
    "  F is exactly one of: bio, research_area, award, experience, publication, software, group\n"
    "  V is the concise fact (e.g. an award name+year, a paper title, a software tool name)\n"
    "  E is a VERBATIM quote copied character-for-character from the page that states the fact.\n"
    "Repeat the field for multiple facts of the same kind (list every award separately).\n"
    "CRITICAL RULES:\n"
    "- Output a flat ARRAY, never an object keyed by field.\n"
    "- Copy E exactly from the page. Do NOT paraphrase, summarize, translate, or fix it.\n"
    "- The evidence E must actually STATE the value V, not just mention the topic.\n"
    "- If you cannot find a verbatim quote on the page that supports a fact, DO NOT include it.\n"
    "- Extract facts about THIS professor only — never about co-authors or other people.\n"
    "- Never invent awards, dates, titles, or affiliations. Output [] if nothing qualifies."
)


@dataclass
class Fact:
    field: str
    value: str
    evidence: str
    source_url: str


def build_prompt(page_text: str, name: str) -> tuple[str, str]:
    user = (f"Professor: {name}\n\n--- PAGE TEXT START ---\n{page_text}\n"
            f"--- PAGE TEXT END ---\n\nReturn the JSON array now.")
    return SYSTEM, user


def chunk_text(text: str, window: int = WINDOW, overlap: int = OVERLAP) -> list[str]:
    """Overlapping windows over the whole page — no content dropped."""
    text = text.strip()
    if len(text) <= window:
        return [text] if text else []
    step = max(1, window - overlap)
    return [text[i:i + window] for i in range(0, len(text), step) if text[i:i + window].strip()]


def _loads_any(raw: str):
    """Parse a JSON value from a possibly-noisy LLM response (whole thing, or the
    first array/object embedded in prose/code-fences)."""
    raw = raw.strip()
    for candidate in (raw, *(m.group(0) for m in re.finditer(r"(\[.*\]|\{.*\})", raw, re.DOTALL))):
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            continue
    return None


def parse_facts(raw: str) -> list[dict]:
    """Coerce an LLM response into a flat list of fact dicts. Tolerates the shapes
    models actually emit: a flat array; a {"facts": [...]} wrapper; or an object
    keyed by field ({"award": {...}} or {"award": [ {...}, {...} ]})."""
    if not raw:
        return []
    obj = _loads_any(raw)
    return _coerce(obj)


def _coerce(obj) -> list[dict]:
    out: list[dict] = []
    if isinstance(obj, list):
        out = [d for d in obj if isinstance(d, dict) and "field" in d]
    elif isinstance(obj, dict):
        if isinstance(obj.get("facts"), list):
            return _coerce(obj["facts"])
        if "field" in obj:                      # a single fact object
            return [obj]
        for key, val in obj.items():            # object keyed by field
            if isinstance(val, dict):
                out.append({"field": key, **val})
            elif isinstance(val, list):
                out += [{"field": key, **d} for d in val if isinstance(d, dict)]
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def is_grounded(evidence: str, page_text: str) -> bool:
    """True iff the evidence quote is literally present on the page (whitespace- and
    case-insensitive, but otherwise verbatim)."""
    ev = _norm(evidence)
    return len(ev) >= MIN_EVIDENCE and ev in _norm(page_text)


def extract_page(page_text: str, name: str, source_url: str, call_llm) -> list[Fact]:
    """Crawled page -> grounded Facts. ``call_llm(system, user) -> str`` is injected.
    Every kept fact's evidence is verifiably on this page."""
    kept: list[Fact] = []
    seen: set[tuple[str, str]] = set()
    for chunk in chunk_text(page_text):
        system, user = build_prompt(chunk, name)
        for d in parse_facts(call_llm(system, user)):
            field = str(d.get("field", "")).strip().lower()
            value = str(d.get("value", "")).strip()
            evidence = str(d.get("evidence", "")).strip()
            if field not in ALLOWED_FIELDS or not value:
                continue
            if not is_grounded(evidence, page_text):   # the anti-hallucination gate
                continue
            fact = _refine(Fact(field=field, value=value, evidence=evidence,
                                source_url=source_url))
            if fact is None:                            # quality pass dropped it
                continue
            key = (fact.field, _norm(fact.value))
            if key in seen:
                continue
            seen.add(key)
            kept.append(fact)
    return kept
