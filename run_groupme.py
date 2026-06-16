"""GroupMe bot entry point — runs independently from the Discord and Telegram bots.

Mirrors run_telegram.py: builds the shared assistant brain via build_assistant() so the
retriever/LLM/conversation wiring is identical across platforms, then drives the GroupMe
connector (polling). Enable by setting GROUPME_ENABLED=true (and GROUPME_ACCESS_TOKEN for
inbound) in .env.
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bot.config import config
from bot.connectors.groupme_connector import GroupMeConnector
from bot.services.database import Database
from bot.services.knowledge_base import KnowledgeBase
from bot.services.moderation import RateLimiter


def _configure_logging() -> None:
    level = getattr(logging, config.log_level, logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(logging.FileHandler("groupme_bot.log", encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=level, format=fmt, handlers=handlers)


_configure_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    if not config.groupme_enabled:
        logger.error("GROUPME_ENABLED is not true in .env — not starting GroupMe bot")
        sys.exit(1)
    if not config.groupme_bot_id:
        logger.error("GROUPME_BOT_ID is not set in .env — cannot start GroupMe bot")
        sys.exit(1)

    # ── Services (mirrors run_telegram.py / bot/main.py setup_hook) ───────────
    db = Database(config.database_path)
    db.connect()
    db.init_tables()
    db.migrate_events_columns()
    db.migrate_rag_columns()

    kb = KnowledgeBase(data_dir=config.data_dir)
    kb.load()

    rate_limiter = RateLimiter(max_calls=5, period_seconds=60)

    from bot.core.assistant import build_assistant
    asst = await build_assistant(config, db, kb, rate_limiter)
    handler = asst.message_handler
    logger.info("Assistant wired (GroupMe)")

    connector = GroupMeConnector(
        bot_id=config.groupme_bot_id,
        access_token=config.groupme_access_token,
        group_id=config.groupme_group_id,
        handler=handler,
        kb=kb,
        poll_interval=config.groupme_poll_interval,
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
        logger.info("GroupMe bot shut down cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
