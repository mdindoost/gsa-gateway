"""Fuzzy search over the GSA knowledge base using rapidfuzz."""

import logging
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from bot.services.knowledge_base import Event, KnowledgeBase

logger = logging.getLogger(__name__)

MIN_CONFIDENCE = 60.0
EVENT_MIN_CONFIDENCE = 45.0
CONTACT_MIN_CONFIDENCE = 45.0


@dataclass
class SearchResult:
    """A single knowledge-base match with metadata."""

    text: str
    content: str
    score: float
    source_type: str
    section: str


class SearchService:
    """Fuzzy-search wrapper around a loaded KnowledgeBase."""

    def __init__(self, kb: KnowledgeBase, min_confidence: float = MIN_CONFIDENCE) -> None:
        self.kb = kb
        self.min_confidence = min_confidence

    def search(self, query: str, limit: int = 3) -> list[SearchResult]:
        """Return up to *limit* FAQ matches above the confidence threshold.

        Results are ordered by score descending.
        """
        items = self.kb.get_searchable_texts()
        if not items:
            return []

        # Build id → text mapping for rapidfuzz
        choices = {item["id"]: item["text"] for item in items}
        id_map = {item["id"]: item for item in items}

        matches = process.extract(
            query,
            choices,
            scorer=fuzz.token_set_ratio,
            limit=limit,
        )

        results: list[SearchResult] = []
        for _matched_text, score, key in matches:
            if score >= self.min_confidence:
                item = id_map[key]
                results.append(
                    SearchResult(
                        text=item["text"],
                        content=item["content"],
                        score=float(score),
                        source_type=item["type"],
                        section=item["section"],
                    )
                )
        return results

    def search_events(self, name: str) -> list[tuple[Event, float]]:
        """Return events whose name fuzzy-matches *name*."""
        if not self.kb.events:
            return []

        event_names = [ev.name for ev in self.kb.events]
        matches = process.extract(
            name,
            event_names,
            scorer=fuzz.token_set_ratio,
            limit=3,
        )

        results: list[tuple[Event, float]] = []
        for _ev_name, score, idx in matches:
            if score >= EVENT_MIN_CONFIDENCE:
                results.append((self.kb.events[idx], float(score)))
        return results

    def search_contacts(self, role_query: str) -> tuple | None:
        """Return the best-matching (Contact, score) pair or None."""
        if not self.kb.contacts:
            return None

        # Search by canonical key (e.g. "vp_academic_affairs")
        key_match = process.extractOne(
            role_query.lower().replace(" ", "_"),
            list(self.kb.contacts.keys()),
            scorer=fuzz.token_set_ratio,
        )
        if key_match and key_match[1] >= CONTACT_MIN_CONFIDENCE:
            return (self.kb.contacts[key_match[0]], float(key_match[1]))

        # Fallback: search by human-readable role name
        role_map = {k: v.role for k, v in self.kb.contacts.items()}
        role_match = process.extractOne(
            role_query,
            role_map,
            scorer=fuzz.token_set_ratio,
        )
        if role_match and role_match[1] >= CONTACT_MIN_CONFIDENCE:
            return (self.kb.contacts[role_match[2]], float(role_match[1]))

        return None
