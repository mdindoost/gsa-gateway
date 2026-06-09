"""Telegram bot entry point — runs independently from the Discord bot."""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bot.config import config
from bot.connectors.telegram_connector import TelegramConnector
from bot.core.message_handler import MessageHandler
from bot.services.conversation import ConversationManager
from bot.services.database import Database
from bot.services.embedder import EmbeddingService
from bot.services.intent_detector import IntentDetector
from bot.services.knowledge_base import KnowledgeBase
from bot.services.moderation import RateLimiter
from bot.services.retriever import Retriever
from bot.services.vector_store import VectorStore


def _configure_logging() -> None:
    level = getattr(logging, config.log_level, logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(logging.FileHandler("telegram_bot.log", encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=level, format=fmt, handlers=handlers)


_configure_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    # Token check must happen before any resources are allocated
    # (sys.exit bypasses the finally block)
    if not config.telegram_token:
        logger.error("TELEGRAM_TOKEN is not set in .env — cannot start Telegram bot")
        sys.exit(1)

    # ── Services (mirrors bot/main.py setup_hook) ────────────────────────────
    db = Database(config.database_path)
    db.connect()
    db.init_tables()
    db.migrate_events_columns()
    db.migrate_rag_columns()

    kb = KnowledgeBase(data_dir=config.data_dir)
    kb.load()

    rate_limiter = RateLimiter(max_calls=5, period_seconds=60)
    conversation_manager = ConversationManager(
        timeout_minutes=config.conversation_timeout_minutes,
        max_turns=config.conversation_max_turns,
    )
    intent_detector = IntentDetector()

    embedder = EmbeddingService(base_url=config.ollama_url, model=config.embedding_model)
    embed_ok = await embedder.check_connection()
    if not embed_ok:
        logger.warning("Embedding model unavailable — semantic search disabled")
        embedder = None

    # Retriever choice mirrors bot/main.py "Wire A": V2 shim (SQLite hybrid KB)
    # when the flag is on, else the v1 ChromaDB retriever. Keeps Telegram and
    # Discord on the SAME knowledge base.
    import os
    if os.getenv("V2_RETRIEVER_ENABLED", "false").lower() == "true":
        from v2.integration.retriever_shim import V2RetrieverShim
        from v2.core.retrieval.embedder import Embedder as V2Embedder
        retriever = V2RetrieverShim(db_path="gsa_gateway.db", embedder=V2Embedder())
        logger.info("V2 Retriever active (Telegram)")
    else:
        vector_store = VectorStore(db_path=config.chroma_db_path)
        retriever = None
        if embedder and not vector_store.is_empty():
            retriever = Retriever(embedder=embedder, vector_store=vector_store)
            logger.info("V1 Retriever active (Telegram): %d chunks", vector_store.get_chunk_count())
        else:
            logger.warning("Retriever not initialized — falling back to keyword search")

    ollama = None
    if config.ollama_enabled:
        from bot.services.ollama_client import OllamaClient
        ollama = OllamaClient(
            base_url=config.ollama_url,
            model=config.ollama_model,
            timeout=config.ollama_timeout,
            embedding_model=config.embedding_model,
        )
        await ollama.check_connection()
        logger.info("Ollama client initialized (model=%s)", config.ollama_model)

    handler = MessageHandler(
        retriever=retriever,
        ollama=ollama,
        conversation_manager=conversation_manager,
        intent_detector=intent_detector,
        db=db,
        rate_limiter=rate_limiter,
        kb=kb,
        config=config,
    )

    connector = TelegramConnector(
        token=config.telegram_token, handler=handler, kb=kb
    )
    await connector.setup_services()

    try:
        await connector.start()
    except asyncio.CancelledError:
        pass
    finally:
        await connector.stop()
        if embedder:
            await embedder.close()
        if ollama:
            await ollama.close()
        db.close()
        logger.info("Telegram bot shut down cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
