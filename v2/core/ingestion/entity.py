"""Normalized entity record + the items it decomposes into.

An entity (a faculty member, office, club) is captured once as an ``EntityRecord``,
then DECOMPOSED into many small, self-contained ``KItem``s — one ``profile`` plus a
``research_statement``, one item per publication/award, etc. (small-to-big
retrieval). There are no content caps: each item is naturally focused.

The structural link from an item back to its entity lives in ``metadata.entity_id``
— ``parent_id``/``root_id`` stay reserved for the existing versioning scheme.
See docs/superpowers/specs/2026-06-11-hybrid-knowledge-ingestion.md (§7b).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Publication:
    title: str
    venue: str = ""
    year: str = ""
    url: str = ""


@dataclass
class EntityRecord:
    """One captured entity, before decomposition. Fields are uncapped lists/text."""
    entity_id: str                                   # stable key (e.g. profile URL/slug)
    name: str
    org: str = ""                                    # e.g. "Computer Science"
    source_url: str = ""
    verified: bool = True                            # True for the authoritative precise crawl
    titles: list[str] = field(default_factory=list)
    role: str = ""
    research_statement: str = ""
    research_areas: list[str] = field(default_factory=list)
    publications: list[Publication] = field(default_factory=list)
    awards: list[str] = field(default_factory=list)
    teaching: list[str] = field(default_factory=list)
    service: list[str] = field(default_factory=list)
    education: list[str] = field(default_factory=list)
    links: dict = field(default_factory=dict)        # website, scholar, ...
    contact: dict = field(default_factory=dict)      # email, office, phone


@dataclass
class KItem:
    """A single decomposed item, ready to persist into ``knowledge_items``.

    ``natural_key`` is the stable per-item identity used by the reconcile step
    (entity_id + type + a within-entity key), so re-crawls can diff/version each
    item independently instead of one upsert per source_url.
    """
    type: str                                        # profile|research_statement|publication|...
    title: str
    content: str                                     # typed-context-prefixed, self-contained
    natural_key: str
    metadata: dict = field(default_factory=dict)     # {entity_id, verified, ...}
    source_url: str = ""
