"""Phase 1b — fold grounded personal-site facts into an entity's item set.

Each :class:`Fact` from the web extractor becomes a KItem (same shape as the NJIT
adapter's), carrying its own ``source_url`` (the personal page) and its verbatim
``evidence`` in metadata for traceability. ``verified=True`` — per the decision that
a professor's own site is treated as true.

We take from the web only the fields it genuinely ADDS (awards, experience, software,
projects, groups, service, extra publications); NJIT stays authoritative for bio and
research areas. The merge dedups by ``natural_key`` so a publication already captured
from NJIT isn't duplicated — the NJIT (institutional) item wins.
"""
from __future__ import annotations

import hashlib
import re

from v2.core.ingestion.entity import KItem
from v2.core.ingestion.web_extract import Fact

# web field -> knowledge_items type. bio/research_area are intentionally absent
# (NJIT is authoritative). 'publication' is also absent: NJIT already holds the
# publication list, and a web pub's natural_key (sha1 of the model's bare title)
# would never match NJIT's (sha1 of the full citation), so it can't be deduped and
# would just duplicate papers. The web's value is awards/experience/service/software.
_FIELD_TYPE = {
    "award": "award", "experience": "experience",
    "software": "software", "project": "project", "group": "group", "service": "service",
}
_PREFIX = {
    "award": "Award received by", "experience": "Career history of",
    "software": "Software by", "project": "Project by",
    "group": "Group affiliation of", "service": "Service by",
}


def _key(s: str) -> str:
    # normalize (collapse whitespace, strip trailing punctuation) before hashing so a
    # trivial wording drift between refreshes doesn't fork a "new" item.
    norm = re.sub(r"\s+", " ", s).strip().lower().rstrip(".,;:")
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def facts_to_items(facts: list[Fact], entity_id: str, subject: str) -> list[KItem]:
    """Grounded facts -> KItems (verified=True, source_url = the personal page,
    evidence kept in metadata). Deduped within the web set by natural_key."""
    items: list[KItem] = []
    seen: set[str] = set()
    for f in facts:
        t = _FIELD_TYPE.get(f.field)
        if not t or not f.value.strip():
            continue
        nk = f"{entity_id}:{t}:{_key(f.value)}"
        if nk in seen:
            continue
        seen.add(nk)
        items.append(KItem(
            type=t, title=f.value.strip()[:200],
            content=f"{_PREFIX[t]} {subject}: {f.value.strip()}",
            natural_key=nk,
            metadata={"entity_id": entity_id, "verified": True, "evidence": f.evidence},
            source_url=f.source_url,
        ))
    return items


def merge(njit_items: list[KItem], web_items: list[KItem]) -> list[KItem]:
    """Union NJIT + web items, deduped by natural_key. NJIT wins on a collision
    (e.g. the same publication on both sources) — it's the institutional source."""
    out = list(njit_items)
    keys = {i.natural_key for i in njit_items}
    for w in web_items:
        if w.natural_key in keys:
            continue
        keys.add(w.natural_key)
        out.append(w)
    return out
