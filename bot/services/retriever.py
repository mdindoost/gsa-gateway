"""RAG retriever — embeds a query and returns the most relevant KB chunks."""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from bot.services.embedder import EmbeddingService
from bot.services.vector_store import VectorStore

logger = logging.getLogger(__name__)

MIN_SIMILARITY = 0.3
TOP_K_RETRIEVAL = 15
TOP_K_FINAL = 7

SOURCE_FRIENDLY_NAMES = {
    "gsa_faq.md": "GSA FAQ",
    "gsa_constitution.md": "GSA Constitution & Bylaws",
    "travel_award.md": "Travel Award Guide",
    "club_finance.md": "Club Financial Bylaws",
    "rules.md": "GSA Community Rules",
    "mmi_workshop.md": "MMI Workshop Series",
    "bot_features.md": "GSA Gateway Bot Guide",
    "events.yml": "GSA Events",
    "contacts.yml": "GSA Contacts",
    "resources.yml": "GSA Resources",
}

_STOP_WORDS = {
    "the", "is", "are", "what", "how", "do", "does", "i", "a", "an", "in",
    "of", "to", "for", "and", "or", "that", "this", "it", "be", "was",
    "will", "can", "if", "my", "me", "we", "you", "your", "at", "on",
    "with", "by", "as", "from", "have", "has", "been", "would", "could",
    "should", "which", "there", "their", "about", "more", "also",
}


@dataclass
class RetrievedChunk:
    text: str
    source_file: str
    source_type: str
    section_title: str
    similarity: float
    relevance_score: float
    metadata: dict = field(default_factory=dict)


class Retriever:
    def __init__(
        self,
        embedder: EmbeddingService,
        vector_store: VectorStore,
    ) -> None:
        self.embedder = embedder
        self.vector_store = vector_store

    def _build_search_query(
        self,
        current_question: str,
        conversation_history: Optional[list[dict]] = None,
    ) -> str:
        # Clean the question
        clean = current_question.strip()
        clean = re.sub(r'<@!?\d+>', '', clean)   # remove @mentions
        clean = re.sub(r'<#\d+>', '', clean)      # remove #channel refs
        clean = clean.strip()

        if not conversation_history:
            logger.debug("Search query: '%s'", clean)
            return clean

        # Extract topic keywords from the last assistant response
        recent_assistant = [
            t["content"] for t in conversation_history
            if t.get("role") == "assistant"
        ][-2:]

        topic_keywords: list[str] = []
        for text in recent_assistant:
            words = re.findall(r'\b[a-zA-Z]{5,}\b', text)
            for w in words:
                if w.lower() not in _STOP_WORDS:
                    topic_keywords.append(w.lower())

        if topic_keywords:
            unique_keywords = list(dict.fromkeys(topic_keywords))[:5]
            enhanced = f"{clean} [context: {' '.join(unique_keywords)}]"
        else:
            enhanced = clean

        logger.debug("Search query: '%s'", enhanced)
        return enhanced

    def _rerank(
        self,
        query: str,
        chunks: list[dict],
    ) -> list[RetrievedChunk]:
        query_words = {
            w.lower() for w in re.findall(r'\b\w+\b', query)
            if w.lower() not in _STOP_WORDS
        }

        results: list[RetrievedChunk] = []
        for chunk in chunks:
            base_score = chunk["similarity"]
            text_lower = chunk["text"].lower()

            # Keyword match bonus — word boundary to avoid "fun" matching "funding"
            keyword_hits = sum(
                1 for kw in query_words
                if re.search(rf'\b{re.escape(kw)}\b', text_lower)
            )
            keyword_bonus = min(keyword_hits * 0.05, 0.25)

            # Source type bonus
            source_type_bonus = {
                "faq": 0.05,
                "policy": 0.03,
                "event": 0.02,
                "contact": 0.02,
                "resource": 0.01,
            }.get(chunk.get("source_type", ""), 0.0)

            # Section title match bonus
            section_title = chunk.get("section_title", "").lower()
            title_bonus = 0.10 if any(kw in section_title for kw in query_words) else 0.0

            relevance_score = min(
                1.0,
                base_score + keyword_bonus + source_type_bonus + title_bonus,
            )

            results.append(RetrievedChunk(
                text=chunk["text"],
                source_file=chunk.get("source_file", ""),
                source_type=chunk.get("source_type", ""),
                section_title=chunk.get("section_title", ""),
                similarity=chunk["similarity"],
                relevance_score=relevance_score,
                metadata=chunk.get("metadata", {}),
            ))

        results.sort(key=lambda x: x.relevance_score, reverse=True)
        return results[:TOP_K_FINAL]

    def _include_siblings(self, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """For any multi-part chunk in the results, include all its siblings.

        A Q&A answer that exceeds MAX_TOKENS is split into continuation chunks
        tagged with qa_base_id. If only one part is retrieved, the LLM sees an
        incomplete answer. This method fetches and appends any missing parts.
        """
        seen_texts: set[str] = {c.text for c in chunks}
        seen_qa_bases: set[str] = set()
        extra: list[RetrievedChunk] = []

        for chunk in chunks:
            qa_base_id = chunk.metadata.get("qa_base_id")
            if not qa_base_id or qa_base_id in seen_qa_bases:
                continue
            seen_qa_bases.add(qa_base_id)

            for sibling in self.vector_store.get_sibling_chunks(qa_base_id):
                if sibling["text"] in seen_texts:
                    continue
                seen_texts.add(sibling["text"])
                extra.append(RetrievedChunk(
                    text=sibling["text"],
                    source_file=sibling["source_file"],
                    source_type=sibling["source_type"],
                    section_title=sibling["section_title"],
                    similarity=chunk.similarity,
                    relevance_score=chunk.relevance_score,
                    metadata=sibling["metadata"],
                ))

        if extra:
            logger.debug("Sibling expansion added %d chunk(s) for multi-part Q&A", len(extra))

        return chunks + extra

    async def retrieve(
        self,
        query: str,
        conversation_history: Optional[list[dict]] = None,
        source_type_filter: Optional[str] = None,
    ) -> list[RetrievedChunk]:
        search_query = self._build_search_query(query, conversation_history)

        query_embedding = await self.embedder.embed_query(search_query)
        if query_embedding is None:
            logger.error("Embedding failed for query: '%s'", query[:80])
            return []

        raw_results = self.vector_store.query(
            query_embedding=query_embedding,
            n_results=TOP_K_RETRIEVAL,
            source_type_filter=source_type_filter,
        )

        filtered = [r for r in raw_results if r["similarity"] >= MIN_SIMILARITY]
        if not filtered:
            logger.warning(
                "No results above MIN_SIMILARITY threshold for query: '%s'", query[:80]
            )
            return []

        final_chunks = self._include_siblings(self._rerank(query, filtered))

        logger.info(
            "Retrieved %d chunks for query: '%s'", len(final_chunks), query[:50]
        )
        for chunk in final_chunks:
            logger.debug(
                "  [%.2f] %s: %s",
                chunk.relevance_score,
                chunk.source_file,
                chunk.section_title[:50],
            )

        return final_chunks

    async def retrieve_for_food_query(self) -> list[RetrievedChunk]:
        food_query = "free food lunch snacks refreshments catering meal provided"
        return await self.retrieve(
            query=food_query,
            source_type_filter="event",
        )
