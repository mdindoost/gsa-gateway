"""V2RetrieverShim — drop-in replacement for the v1 Retriever.

Exposes the *exact* v1 ``Retriever`` interface (``retrieve``,
``retrieve_for_food_query``, ``rebuild_bm25_index``) but is backed by the v2
hybrid retriever over ``knowledge_items``/``knowledge_vectors``. Because it
quacks like the v1 retriever and returns v1-shaped chunks, neither
``message_handler.py`` nor ``ask.py`` need any changes — ``bot/main.py`` simply
constructs this instead of the v1 ``Retriever`` when ``V2_RETRIEVER_ENABLED``.

The v2 retriever is synchronous + blocking (Ollama embed + sqlite), so calls run
in a thread (``asyncio.to_thread``) with a fresh per-call connection. A semaphore
of 1 serializes the v2 path so it never contends on the sqlite file with v1.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from v2.core.database.schema import get_connection
from v2.core.retrieval.retriever import V2Retriever

logger = logging.getLogger(__name__)

# v1 source_type_filter → v2 knowledge_items.type
_FILTER_MAP = {"contact": ["contact"], "event": ["event_info"], "event_info": ["event_info"]}


@dataclass
class V1Chunk:
    """Same attribute surface the v1 Retriever returns and ollama_client reads."""
    text: str
    source_file: str
    source_type: str
    section_title: str
    similarity: float
    relevance_score: float
    metadata: dict = field(default_factory=dict)


class V2RetrieverShim:
    def __init__(self, db_path: str, embedder, org_id: int | None = None, max_concurrency: int = 4):
        self.db_path = db_path
        self.embedder = embedder
        self.org_id = org_id  # None = search the whole org tree
        self._sem = asyncio.Semaphore(max_concurrency)  # serialize v2 sqlite access

    # ── v1 Retriever interface ────────────────────────────────────────────────
    async def retrieve(self, query, conversation_history=None, source_type_filter=None):
        # Retrieval uses the CURRENT question only. Conversation history is for
        # GENERATION context (Ollama, via message_handler) — prepending it here
        # pollutes the search query on topic switches (e.g. MMI → finances).
        item_types = _FILTER_MAP.get(source_type_filter) if source_type_filter else None
        async with self._sem:
            return await asyncio.to_thread(self._retrieve_sync, query, item_types)

    async def retrieve_for_food_query(self):
        # Food handling uses get_food_events(kb, db) directly, not retriever chunks.
        return []

    def rebuild_bm25_index(self):
        # v2 FTS is rebuilt out-of-band by scripts/rebuild_index.py; no-op here.
        logger.info("V2RetrieverShim.rebuild_bm25_index() is a no-op (use rebuild_index.py)")

    # ── internals ─────────────────────────────────────────────────────────────
    def _retrieve_sync(self, query, item_types):
        conn = get_connection(self.db_path)  # fresh, sqlite-vec loaded, this thread only
        try:
            retriever = V2Retriever(conn, self.embedder)
            results = retriever.retrieve(query, org_id=self.org_id, item_types=item_types, limit=5)
            return [self._to_v1(c) for c in results]
        except Exception:  # noqa: BLE001 - never break the answer path; fall back to empty
            logger.exception("V2 retrieval failed for query: %s", query[:80])
            return []
        finally:
            conn.close()

    @staticmethod
    def _to_v1(c) -> V1Chunk:
        source = c.org_path.split(" > ")[-1] if c.org_path else "GSA"
        rel = c.similarity if c.similarity is not None else 0.7  # keyword-only hits
        return V1Chunk(
            text=c.content,
            source_file=source,
            source_type=c.type,
            section_title=c.title or "",
            similarity=c.similarity or 0.0,
            relevance_score=rel,
            metadata={"org_path": c.org_path, "source": c.source},
        )
