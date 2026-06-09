"""Telegram bot entry point — runs independently from the Discord bot."""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bot.config import config
from bot.connectors.telegram_connector import TelegramConnector
from bot.services.database import Database
from bot.services.knowledge_base import KnowledgeBase
from bot.services.moderation import RateLimiter


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

    # Shared brain — identical wiring to the Discord process (retriever, LLM,
    # conversation, handler). One source of truth; see
    # docs/designs/2026-06-09-unified-assistant.md
    from bot.core.assistant import build_assistant
    asst = await build_assistant(config, db, kb, rate_limiter)
    handler = asst.message_handler
    logger.info("Assistant wired (Telegram)")

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
        if asst.embedder:
            await asst.embedder.close()
        if asst.ollama:
            await asst.ollama.close()
        db.close()
        logger.info("Telegram bot shut down cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
