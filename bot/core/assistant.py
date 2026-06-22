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
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

import bot.config as botcfg
from v2.core.retrieval.route_exemplars import build_classifier, encoder_stamp, verify_stamp
from v2.core.retrieval.unified_router import UnifiedRouter

logger = logging.getLogger(__name__)


def maybe_build_unified_router(db_path, embedder, intent_detector):
    """Build the v2.1 UnifiedRouter ONLY when ROUTER_V21 is on; else None (zero overhead).
    The classifier is FIT here (encodes the ~500 train exemplars once at startup via the
    embedder's batch path — see route_exemplars._encode_prefixed). The router holds db_path,
    NOT a live connection: it opens a short-lived sqlite connection per decide() inside _route."""
    if not botcfg.ROUTER_V21:
        return None
    verify_stamp(embedder, encoder_stamp(embedder))      # fail loudly on encoder drift BEFORE fitting
    t0 = time.time()
    fit_conn = sqlite3.connect(db_path)                  # snapshot conn for the masker + exemplar fit
    try:
        clf = build_classifier(fit_conn, embedder)
    finally:
        fit_conn.close()
    logger.info("router-v21 classifier fit in %.2fs", time.time() - t0)
    return UnifiedRouter(db_path=db_path, classifier=clf, intent_detector=intent_detector)


@dataclass
class Assistant:
    ollama: Any | None
    embedder: Any | None
    vector_store: Any | None
    conversation_manager: Any
    intent_detector: Any
    retriever: Any | None
    message_handler: Any
    # The single per-process source of truth for the gsa/free conversation mode. Shared with
    # the ModeRegistry/ModeDispatcher a connector builds (so judging + conversation read/write
    # the SAME store). See docs/superpowers/specs/2026-06-19-unify-modes-design.md
    mode_store: Any = None


async def build_assistant(config, db, kb, rate_limiter) -> Assistant:
    """Wire the full brain once. db/kb/rate_limiter are passed in (each process
    owns those; other cogs need direct references)."""
    from bot.core.message_handler import MessageHandler
    from bot.core.modes import ConversationModeStore
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

    mode_store = ConversationModeStore()
    conversation_manager = ConversationManager(
        timeout_minutes=config.conversation_timeout_minutes,
        max_turns=config.conversation_max_turns,
        mode_store=mode_store,
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

    # ── Kavosh v2.1 UnifiedRouter (flag-gated; None unless ROUTER_V21) ─────────
    unified_router = None
    if botcfg.ROUTER_V21:
        from v2.core.retrieval.embedder import Embedder as V2Embedder
        try:
            # Use the SAME db path the handler's structured path reads (db.db_path), so the
            # classifier/masker + _route and _structured_from_route never query different DBs
            # (e.g. when DATABASE_PATH is overridden) — review F5.
            router_db_path = getattr(db, "db_path", None) or config.database_path
            unified_router = maybe_build_unified_router(
                db_path=router_db_path, embedder=V2Embedder(), intent_detector=intent_detector)
        except Exception:  # noqa: BLE001 - never block startup; router stays off on failure
            logger.exception("router-v21 build failed; falling back to legacy routing")

    message_handler = MessageHandler(
        retriever=retriever, ollama=ollama, conversation_manager=conversation_manager,
        intent_detector=intent_detector, db=db, rate_limiter=rate_limiter, kb=kb, config=config,
        unified_router=unified_router,
    )
    logger.info("Assistant brain built (retriever=%s)",
                type(retriever).__name__ if retriever else None)

    return Assistant(
        ollama=ollama, embedder=embedder, vector_store=vector_store,
        conversation_manager=conversation_manager, intent_detector=intent_detector,
        retriever=retriever, message_handler=message_handler, mode_store=mode_store,
    )
