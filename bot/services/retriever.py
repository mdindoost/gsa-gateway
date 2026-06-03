"""RAG retriever — hybrid BM25 + vector search with reciprocal rank fusion."""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from bot.services.bm25_index import BM25Index
from bot.services.embedder import EmbeddingService
from bot.services.vector_store import VectorStore

logger = logging.getLogger(__name__)

MIN_SIMILARITY = 0.3
TOP_K_RETRIEVAL = 20   # candidates from each of vector and BM25 before fusion
TOP_K_FINAL = 7        # chunks passed to the LLM after reranking
RRF_K = 60             # reciprocal rank fusion constant (standard default)

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
        self._bm25: Optional[BM25Index] = None
        self._build_bm25_index()

    # ── BM25 index lifecycle ─────────────────────────────────────────────────

    def _build_bm25_index(self) -> None:
        chunks = self.vector_store.get_all_chunks()
        if chunks:
            self._bm25 = BM25Index(chunks)
        else:
            logger.warning("BM25 index not built — vector store is empty")
            self._bm25 = None

    def rebuild_bm25_index(self) -> None:
        """Rebuild the BM25 index after the vector store has been updated.
        Called by /admin_rebuild_index after new chunks are added.
        """
        self._build_bm25_index()
        count = len(self._bm25.chunks) if self._bm25 else 0
        logger.info("BM25 index rebuilt: %d chunks", count)

    # ── Reciprocal rank fusion ───────────────────────────────────────────────

    @staticmethod
    def _reciprocal_rank_fusion(
        vector_results: list[dict],
        bm25_results: list[dict],
    ) -> list[dict]:
        """Merge two ranked lists into one using reciprocal rank fusion.

        Each chunk receives score = Σ 1/(RRF_K + rank) across both lists.
        Chunks in both lists are boosted; BM25-only chunks (exact term matches
        the vector model missed) are included with a fair base score.

        The normalized RRF score is stored as "rrf_score" so the reranker
        uses it as the base instead of the raw vector similarity — this
        prevents BM25-only chunks from being penalized by the 0.5 placeholder.
        """
        scores: dict[str, float] = {}
        chunk_map: dict[str, dict] = {}

        for rank, chunk in enumerate(vector_results):
            cid = chunk["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            chunk_map[cid] = chunk

        for rank, chunk in enumerate(bm25_results):
            cid = chunk.get("chunk_id", "")
            if not cid:
                continue
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            if cid not in chunk_map:
                chunk_map[cid] = dict(chunk)
                chunk_map[cid].setdefault("similarity", 0.5)

        # Normalize RRF scores to [0.40, 0.95] and store on each chunk so
        # the reranker can use a single consistent base regardless of origin.
        if scores:
            lo = min(scores.values())
            hi = max(scores.values())
            span = (hi - lo) or 1.0
            for cid, chunk in chunk_map.items():
                chunk["rrf_score"] = 0.40 + ((scores[cid] - lo) / span) * 0.55

        return sorted(
            chunk_map.values(),
            key=lambda c: c.get("rrf_score", 0.0),
            reverse=True,
        )

    # ── Query preparation ────────────────────────────────────────────────────

    def _build_search_query(
        self,
        current_question: str,
        conversation_history: Optional[list[dict]] = None,
    ) -> str:
        clean = current_question.strip()
        clean = re.sub(r'<@!?\d+>', '', clean)
        clean = re.sub(r'<#\d+>', '', clean)
        clean = clean.strip()

        if not conversation_history:
            logger.debug("Search query: '%s'", clean)
            return clean

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

    # ── Reranker ─────────────────────────────────────────────────────────────

    def _rerank(
        self,
        query: str,
        chunks: list[dict],
    ) -> list[RetrievedChunk]:
        query_words = {
            w.lower() for w in re.findall(r'\b\w+\b', query)
            if w.lower() not in _STOP_WORDS
        }
        # Proper nouns (capitalized, non-stop) get 3× the bonus of common words.
        # Names like "Singh", "MARCuS", "Gurrin" are the strongest disambiguation
        # signal when many chunks have near-identical cosine similarity scores.
        proper_nouns = {
            w.lower() for w in re.findall(r'\b[A-Z][a-zA-Z]+\b', query)
            if w.lower() not in _STOP_WORDS
        }
        common_words = query_words - proper_nouns

        results: list[RetrievedChunk] = []
        for chunk in chunks:
            # Use rrf_score when available (hybrid search path) so BM25-retrieved
            # chunks get a base score that reflects their rank, not a placeholder.
            base_score = chunk.get("rrf_score", chunk["similarity"])
            text_lower = chunk["text"].lower()

            common_hits = sum(
                1 for kw in common_words
                if re.search(rf'\b{re.escape(kw)}\b', text_lower)
            )
            proper_hits = sum(
                1 for pn in proper_nouns
                if re.search(rf'\b{re.escape(pn)}\b', text_lower)
            )
            keyword_bonus = min(common_hits * 0.05 + proper_hits * 0.15, 0.35)

            source_type_bonus = {
                "faq": 0.05,
                "policy": 0.03,
                "event": 0.02,
                "contact": 0.02,
                "resource": 0.01,
            }.get(chunk.get("source_type", ""), 0.0)

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

    # ── Sibling expansion ────────────────────────────────────────────────────

    def _include_siblings(self, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """For any multi-part chunk, include all sibling continuation parts."""
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
            logger.debug("Sibling expansion added %d chunk(s)", len(extra))

        return chunks + extra

    # ── Public retrieve ──────────────────────────────────────────────────────

    async def retrieve(
        self,
        query: str,
        conversation_history: Optional[list[dict]] = None,
        source_type_filter: Optional[str] = None,
    ) -> list[RetrievedChunk]:
        search_query = self._build_search_query(query, conversation_history)

        # Vector search
        query_embedding = await self.embedder.embed_query(search_query)
        if query_embedding is None:
            logger.error("Embedding failed for query: '%s'", query[:80])
            return []

        vector_results = self.vector_store.query(
            query_embedding=query_embedding,
            n_results=TOP_K_RETRIEVAL,
            source_type_filter=source_type_filter,
        )
        vector_results = [r for r in vector_results if r["similarity"] >= MIN_SIMILARITY]

        # BM25 search (lexical — catches exact/sparse terms the embedding misses)
        bm25_results: list[dict] = []
        if self._bm25 and not source_type_filter:
            bm25_results = self._bm25.search(search_query, n_results=TOP_K_RETRIEVAL)

        if not vector_results and not bm25_results:
            logger.warning("No results for query: '%s'", query[:80])
            return []

        # Fuse, rerank, expand siblings
        if bm25_results:
            fused = self._reciprocal_rank_fusion(vector_results, bm25_results)
            logger.debug(
                "Hybrid retrieval: %d vector + %d BM25 -> %d fused candidates",
                len(vector_results), len(bm25_results), len(fused),
            )
        else:
            fused = vector_results

        final_chunks = self._include_siblings(self._rerank(query, fused))

        logger.info("Retrieved %d chunks for query: '%s'", len(final_chunks), query[:50])
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
