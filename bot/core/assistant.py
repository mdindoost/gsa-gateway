"""Single source of truth for wiring the assistant "brain".

Both entry points -- bot/main.py (Discord) and run_telegram.py (Telegram) --
construct their MessageHandler through build_assistant(), so the retriever, LLM,
and conversation wiring can never drift between platforms again. (That drift is
what left Telegram on the v1 ChromaDB retriever while Discord used v2.)

Per-user conversation isolation is preserved: ConversationManager keys sessions
by user_id, one manager per process. Discord and Telegram run as separate
processes with their own loops, so a Discord id and a Telegram id never collide;
if they were ever merged into one process, sessions should key by
f"{platform}:{user_id}".
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Assistant:
    ollama: Any | None
    embedder: Any | None
    vector_store: Any | None
    conversation_manager: Any
    intent_detector: Any
    retriever: Any | None
    message_handler: Any


async def build_assistant(config, db, kb, rate_limiter) -> Assistant:
    """Wire the full brain once. db/kb/rate_limiter are passed in (each process
    owns those; other cogs need direct references)."""
    from bot.core.message_handler import MessageHandler
    from bot.services.conversation import ConversationManager
    from bot.services.intent_detector import IntentDetector

    # ── LLM ──────────────────────────────────────────────────────────────────
    ollama = None
    if config.ollama_enabled:
        from bot.services.ollama_client import OllamaClient
        ollama = OllamaClient(
            base_url=config.ollama_url, model=config.ollama_model,
            timeout=config.ollama_timeout, embedding_model=config.embedding_model,
        )
        await ollama.check_connection()
        logger.info("Ollama client initialised (model=%s)", config.ollama_model)

    conversation_manager = ConversationManager(
        timeout_minutes=config.conversation_timeout_minutes,
        max_turns=config.conversation_max_turns,
    )
    intent_detector = IntentDetector()

    # ── v1 embedder + ChromaDB store (v1 retriever + admin rebuild use these) ──
    from bot.services.embedder import EmbeddingService
    from bot.services.vector_store import VectorStore
    embedder = EmbeddingService(base_url=config.ollama_url, model=config.embedding_model)
    if not await embedder.check_connection():
        logger.warning("Embedding model unavailable — v1 semantic search disabled")
        embedder = None
    vector_store = VectorStore(db_path=config.chroma_db_path)

    # ── Retriever: ONE definition for both platforms ──────────────────────────
    retriever = None
    if os.getenv("V2_RETRIEVER_ENABLED", "false").lower() == "true":
        from v2.core.retrieval.embedder import Embedder as V2Embedder
        from v2.core.retrieval.reranker import CrossEncoderReranker
        from v2.integration.retriever_shim import V2RetrieverShim
        reranker = CrossEncoderReranker()
        try:
            reranker.warm()  # one-time load/download; non-fatal (falls back to RRF order)
        except Exception:  # noqa: BLE001
            logger.warning("reranker warm failed; retrieval uses RRF order")
        retriever = V2RetrieverShim(db_path="gsa_gateway.db", embedder=V2Embedder(),
                                    reranker=reranker)
        logger.info("V2 Retriever active (reranker available=%s)", reranker.available)
    elif embedder and not vector_store.is_empty():
        from bot.services.retriever import Retriever
        retriever = Retriever(embedder=embedder, vector_store=vector_store)
        logger.info("V1 Retriever active: %d chunks", vector_store.get_chunk_count())
    else:
        logger.warning("Retriever not initialized — keyword fallback only")

    message_handler = MessageHandler(
        retriever=retriever, ollama=ollama, conversation_manager=conversation_manager,
        intent_detector=intent_detector, db=db, rate_limiter=rate_limiter, kb=kb, config=config,
    )
    logger.info("Assistant brain built (retriever=%s)",
                type(retriever).__name__ if retriever else None)

    return Assistant(
        ollama=ollama, embedder=embedder, vector_store=vector_store,
        conversation_manager=conversation_manager, intent_detector=intent_detector,
        retriever=retriever, message_handler=message_handler,
    )
