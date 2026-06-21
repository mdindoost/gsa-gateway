"""Slot-masking: resolve the entity span, replace it with a sentinel, THEN embed.

This is the highest-leverage lever for an embedding router (per the Phase-0 bake-off review): when
the org/person token dominates the sentence vector, two different skills over the same org collapse
together. Masking the entity to a constant sentinel makes that direction a constant offset that
cancels out of the cosine argmax, leaving the PATTERN as the signal. Only the resolver-filled entity
slots (org/person) are masked — skill-discriminating words (metric name, role noun, "officers",
"research areas") are deliberately kept.
"""
from __future__ import annotations
import re

ORG = "<ORG>"
PERSON = "<PERSON>"


class SlotMasker:
    """Replace known org/person surface forms in a query with sentinels in a SINGLE pass.

    One compiled alternation (longest term first so "computer science" wins over "science") scanned
    over the ORIGINAL text once, so emitted sentinels are never re-scanned (no "<<ORG>>" corruption
    when a term equals a sentinel bare word) and cost is O(query length), not O(terms x queries).
    """
    def __init__(self, org_terms, person_terms):
        entries = ([(t, ORG) for t in org_terms if t and t.strip()]
                   + [(t, PERSON) for t in person_terms if t and t.strip()])
        # longest first so the alternation prefers the longer phrase at a given position
        entries.sort(key=lambda e: len(e[0]), reverse=True)
        self._term_to_sent = {t.lower(): sent for t, sent in entries}
        self._pattern = None
        if entries:
            alt = "|".join(re.escape(t) for t, _ in entries)
            self._pattern = re.compile(r"(?<!\w)(" + alt + r")(?!\w)", re.IGNORECASE)

    def mask(self, query: str) -> str:
        if self._pattern is None:
            return query
        return self._pattern.sub(lambda m: self._term_to_sent[m.group(0).lower()], query)


class MaskedEncoder:
    """Encoder wrapper: masks entities before delegating. Used transparently for BOTH exemplar fit
    and query predict, so an ExemplarClassifier fit through this is entity-masked end-to-end."""
    def __init__(self, encoder, masker: SlotMasker):
        self.encoder = encoder
        self.masker = masker

    def __call__(self, texts):
        return self.encoder([self.masker.mask(t) for t in texts])


def build_masker_from_db(conn) -> SlotMasker:
    """Build a masker from the live KG: org names + slugs + aliases, person full names + surnames.

    CLI-only (needs the DB); best-effort and defensive so a schema gap never breaks the bake-off.
    """
    org_terms: set[str] = set()
    person_terms: set[str] = set()
    try:
        for (name,) in conn.execute("SELECT name FROM organizations WHERE name IS NOT NULL"):
            if name:
                org_terms.add(name)
        for (slug,) in conn.execute("SELECT slug FROM organizations WHERE slug IS NOT NULL"):
            if slug:
                org_terms.add(slug)
    except Exception:  # noqa: BLE001
        pass
    try:
        import json
        for (meta,) in conn.execute("SELECT metadata FROM organizations WHERE metadata IS NOT NULL"):
            try:
                for a in (json.loads(meta) or {}).get("aliases", []) or []:
                    if a:
                        org_terms.add(a)
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    try:
        for (name,) in conn.execute("SELECT name FROM nodes WHERE type='Person' AND name IS NOT NULL"):
            if not name:
                continue
            person_terms.add(name)
            parts = name.split()
            if len(parts) >= 2:
                person_terms.add(parts[-1])     # surname alone
    except Exception:  # noqa: BLE001
        pass
    # drop ultra-short/ambiguous terms that would over-mask
    org_terms = {t for t in org_terms if len(t) >= 2}
    person_terms = {t for t in person_terms if len(t) >= 3}
    return SlotMasker(sorted(org_terms), sorted(person_terms))
