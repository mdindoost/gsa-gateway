"""Fuzzy search over the GSA knowledge base using rapidfuzz."""

import logging
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from bot.services.knowledge_base import Event, KnowledgeBase

logger = logging.getLogger(__name__)

MIN_CONFIDENCE = 60.0
OLLAMA_MIN_CONFIDENCE = 45.0
EVENT_MIN_CONFIDENCE = 45.0
CONTACT_MIN_CONFIDENCE = 45.0

SYNONYMS: dict[str, list[str]] = {
    "fund":          ["funding", "money", "award", "grant", "travel", "financial"],
    "money":         ["funding", "award", "grant", "financial", "travel"],
    "help":          ["support", "resource", "contact", "assistance", "service"],
    "event":         ["events", "workshop", "mixer", "seminar", "webinar", "meeting"],
    "award":         ["funding", "travel", "prize", "money", "grant", "research day"],
    "international": ["global", "visa", "OGI", "foreign", "exchange"],
    "wellness":      ["health", "mental", "counseling", "coaching", "stress", "support"],
    "research":      ["PhD", "study", "paper", "poster", "3MRP", "publication"],
    "contact":       ["email", "officer", "office", "reach", "talk", "meet"],
    "join":          ["become", "member", "involved", "participate", "volunteer"],
    "officer":       ["president", "VP", "board", "eboard", "leadership", "staff"],
}


def preprocess_query(query: str) -> str:
    """Normalize a query; expand single-word queries to improve search recall."""
    query = query.strip().lower()
    if len(query.split()) == 1:
        query = f"tell me about {query} at GSA NJIT"
    return query


def _expand_query(query: str) -> list[str]:
    """Return query plus synonym terms for the first SYNONYMS keyword found in it."""
    terms = [query]
    for word in query.lower().split():
        if word in SYNONYMS:
            terms.extend(SYNONYMS[word])
            break
    return terms


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

    def search(
        self,
        query: str,
        limit: int = 3,
        min_confidence: float | None = None,
    ) -> list[SearchResult]:
        """Return up to *limit* KB matches above the confidence threshold.

        Pass min_confidence=0 to always return top results regardless of score.
        Synonym expansion is applied automatically for short/vague queries.
        """
        threshold = min_confidence if min_confidence is not None else self.min_confidence
        items = self.kb.get_searchable_texts()
        if not items:
            return []

        choices = {item["id"]: item["text"] for item in items}
        id_map = {item["id"]: item for item in items}

        # Search the query and all synonym expansions; keep best score per item.
        search_terms = _expand_query(query)
        best_scores: dict[str, float] = {}
        for term in search_terms:
            matches = process.extract(
                term,
                choices,
                scorer=fuzz.token_set_ratio,
                limit=limit * 2,
            )
            for _matched_text, score, key in matches:
                if key not in best_scores or score > best_scores[key]:
                    best_scores[key] = score

        results: list[SearchResult] = []
        for key, score in sorted(best_scores.items(), key=lambda x: x[1], reverse=True):
            if score >= threshold:
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
        return results[:limit]

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

        key_match = process.extractOne(
            role_query.lower().replace(" ", "_"),
            list(self.kb.contacts.keys()),
            scorer=fuzz.token_set_ratio,
        )
        if key_match and key_match[1] >= CONTACT_MIN_CONFIDENCE:
            return (self.kb.contacts[key_match[0]], float(key_match[1]))

        role_map = {k: v.role for k, v in self.kb.contacts.items()}
        role_match = process.extractOne(
            role_query,
            role_map,
            scorer=fuzz.token_set_ratio,
        )
        if role_match and role_match[1] >= CONTACT_MIN_CONFIDENCE:
            return (self.kb.contacts[role_match[2]], float(role_match[1]))

        return None
