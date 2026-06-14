"""Decompose an EntityRecord into focused, self-contained KItems (small-to-big).

Each item carries a typed-context prefix (so even abstract-less items embed with
the entity + venue in them), ``metadata.entity_id`` (the structural link), and a
stable ``natural_key`` for reconcile. No content is truncated — one publication =
one item, however many there are.
"""
from __future__ import annotations

import hashlib

from v2.core.ingestion.entity import EntityRecord, KItem


def _key(s: str) -> str:
    return hashlib.sha1(s.strip().lower().encode("utf-8")).hexdigest()[:12]


def _subject(rec: EntityRecord) -> str:
    """Typed-context subject used to prefix every item: '<Name> (<org>)'."""
    return f"{rec.name} ({rec.org})" if rec.org else rec.name


def decompose(rec: EntityRecord) -> list[KItem]:
    items: list[KItem] = []
    base_meta = {"entity_id": rec.entity_id, "verified": rec.verified}

    def mk(item_type: str, title: str, content: str, key_suffix: str,
           extra: dict | None = None) -> KItem:
        meta = dict(base_meta)
        if extra:
            meta.update(extra)
        return KItem(type=item_type, title=title, content=content,
                     natural_key=f"{rec.entity_id}:{item_type}:{key_suffix}",
                     metadata=meta, source_url=rec.source_url)

    subj = _subject(rec)

    # ── profile (the anchor) — always emitted ──────────────────────────────────
    headline = ", ".join(rec.titles) if rec.titles else rec.role
    parts = [f"Profile: {rec.name}"]
    if headline:
        parts.append(headline)
    if rec.org:
        parts.append(rec.org)
    profile = " — ".join(parts)
    if rec.role and rec.role not in profile:
        profile += f". Role: {rec.role}"
    if rec.contact.get("email"):
        profile += f". Email: {rec.contact['email']}"
    if rec.contact.get("office"):
        profile += f". Office: {rec.contact['office']}"
    if rec.links.get("website"):
        profile += f". Website: {rec.links['website']}"
    items.append(mk("profile", rec.name, profile, "main"))

    # ── overview (LLM-written narrative, grounded in the verified facts) ────────
    if rec.overview.strip():
        items.append(mk("overview", f"{rec.name} — Overview",
                        f"Overview of {subj}: {rec.overview.strip()}", "main"))

    # ── biography (the "About" prose) ──────────────────────────────────────────
    if rec.bio.strip():
        items.append(mk("about", f"{rec.name} — About",
                        f"About {subj}: {rec.bio.strip()}", "main"))

    # ── research statement + areas (the topical signal) ────────────────────────
    if rec.research_statement.strip():
        items.append(mk("research_statement", f"{rec.name} — Research",
                        f"Research statement of {subj}: {rec.research_statement.strip()}",
                        "main"))
    if rec.research_areas:
        cleaned = [a.strip() for a in rec.research_areas if a.strip()]
        if cleaned:
            areas = "; ".join(cleaned)
            items.append(mk("research_areas", f"{rec.name} — Research areas",
                            f"Research areas of {subj}: {areas}", "main",
                            extra={"areas": cleaned}))

    # ── publications — one item each, ALL of them (no cap) ─────────────────────
    for p in rec.publications:
        if not p.title.strip():
            continue
        tail = " ".join(x for x in (p.venue.strip(), str(p.year).strip()) if x)
        line = f"Publication by {subj}: {p.title.strip()}" + (f" ({tail})" if tail else "")
        items.append(mk("publication", p.title.strip(), line, _key(p.title),
                        extra={"venue": p.venue, "year": p.year, "url": p.url}))

    # ── awards — one each ──────────────────────────────────────────────────────
    for a in rec.awards:
        if a.strip():
            items.append(mk("award", a.strip(), f"Award received by {subj}: {a.strip()}",
                            _key(a)))

    # ── teaching / service — grouped (a course list is one coherent unit) ──────
    if any(t.strip() for t in rec.teaching):
        courses = "; ".join(t.strip() for t in rec.teaching if t.strip())
        items.append(mk("teaching", f"{rec.name} — Teaching",
                        f"Courses taught by {subj}: {courses}", "main"))
    if any(s.strip() for s in rec.service):
        svc = "; ".join(s.strip() for s in rec.service if s.strip())
        items.append(mk("service", f"{rec.name} — Service",
                        f"Service by {subj}: {svc}", "main"))
    if any(e.strip() for e in rec.education):
        edu = "; ".join(e.strip() for e in rec.education if e.strip())
        items.append(mk("education", f"{rec.name} — Education",
                        f"Education of {subj}: {edu}", "main"))
    if any(e.strip() for e in rec.experience):
        exp = "; ".join(e.strip() for e in rec.experience if e.strip())
        items.append(mk("experience", f"{rec.name} — Experience",
                        f"Career history of {subj}: {exp}", "main"))

    return items
