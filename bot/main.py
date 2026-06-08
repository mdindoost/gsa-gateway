"""GSA Gateway bot entry point — loads all cogs and starts the bot."""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

# Ensure the project root is importable when run as `python bot/main.py`
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.config import config


def _configure_logging() -> None:
    level = getattr(logging, config.log_level, logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(logging.FileHandler("gsa_gateway.log", encoding="utf-8"))
    except OSError:
        pass  # Non-writable filesystem — skip file handler
    logging.basicConfig(level=level, format=fmt, handlers=handlers)


_configure_logging()
logger = logging.getLogger(__name__)

EXTENSIONS = [
    "bot.commands.ask",
    "bot.commands.events",
    "bot.commands.initiative",
    "bot.commands.feedback",
    "bot.commands.resources",
    "bot.commands.contact",
    "bot.commands.help_cmd",
    "bot.commands.admin",
    "bot.commands.qrcode_cmd",
    "bot.services.scheduler",
]


class GSABot(commands.Bot):
    """NJIT Graduate Student Association Discord bot."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True      # privileged — enable in Dev Portal
        intents.presences = True    # privileged — enable in Dev Portal
        super().__init__(
            command_prefix=config.bot_prefix,
            intents=intents,
            help_command=None,  # Custom /help is implemented as a cog
        )
        # Core services — set in setup_hook
        self.db = None
        self.kb = None
        self.search_svc = None
        self.rate_limiter = None
        self.ollama = None
        self.config = config
        # RAG services
        self.embedder = None
        self.vector_store = None
        self.retriever = None
        self.conversation_manager: Optional[object] = None
        self.intent_detector: Optional[object] = None
        # MathCafe
        self.mathcafe = None
        self.telegram_connector = None
        # Message handler
        self.message_handler = None

    async def setup_hook(self) -> None:
        """Initialise services and load extensions before on_ready fires."""
        from bot.services.database import Database
        from bot.services.knowledge_base import KnowledgeBase
        from bot.services.moderation import RateLimiter
        from bot.services.search import SearchService

        self.db = Database(config.database_path)
        self.db.connect()
        self.db.init_tables()
        self.db.migrate_events_columns()
        self.db.migrate_rag_columns()

        self.kb = KnowledgeBase(data_dir=config.data_dir)
        self.kb.load()

        self.search_svc = SearchService(self.kb)
        self.rate_limiter = RateLimiter(max_calls=5, period_seconds=60)

        if config.ollama_enabled:
            from bot.services.ollama_client import OllamaClient
            self.ollama = OllamaClient(
                base_url=config.ollama_url,
                model=config.ollama_model,
                timeout=config.ollama_timeout,
                embedding_model=config.embedding_model,
            )
            logger.info("Ollama client initialised (model=%s)", config.ollama_model)
            await self.ollama.check_connection()

        # ── Conversation manager ─────────────────────────────────────────────
        from bot.services.conversation import ConversationManager
        self.conversation_manager = ConversationManager(
            timeout_minutes=config.conversation_timeout_minutes,
            max_turns=config.conversation_max_turns,
        )
        logger.info("Conversation manager initialized")

        # ── Embedding service ────────────────────────────────────────────────
        from bot.services.embedder import EmbeddingService
        self.embedder = EmbeddingService(
            base_url=config.ollama_url,
            model=config.embedding_model,
        )
        embed_ok = await self.embedder.check_connection()
        if not embed_ok:
            logger.warning(
                "Embedding model not available — semantic search disabled. "
                "Run: ollama pull nomic-embed-text"
            )
            self.embedder = None

        # ── Vector store ─────────────────────────────────────────────────────
        from bot.services.vector_store import VectorStore
        self.vector_store = VectorStore(db_path=config.chroma_db_path)

        if self.vector_store.is_empty():
            logger.warning(
                "Vector store is empty! "
                "Run: python scripts/build_index.py before starting the bot for full RAG support."
            )
        else:
            chunk_count = self.vector_store.get_chunk_count()
            logger.info("Vector store loaded: %d chunks", chunk_count)

        # ── Retriever ────────────────────────────────────────────────────────
        if self.embedder and self.vector_store:
            from bot.services.retriever import Retriever
            self.retriever = Retriever(
                embedder=self.embedder,
                vector_store=self.vector_store,
            )
            logger.info("RAG retriever initialized")
        else:
            logger.warning("Retriever not initialized — falling back to keyword search")

        # ── Intent detector ──────────────────────────────────────────────────
        from bot.services.intent_detector import IntentDetector
        self.intent_detector = IntentDetector()
        logger.info("Intent detector initialized")

        # ── Message handler ──────────────────────────────────────────────────
        from bot.core.message_handler import MessageHandler
        self.message_handler = MessageHandler(
            retriever=self.retriever,
            ollama=self.ollama,
            conversation_manager=self.conversation_manager,
            intent_detector=self.intent_detector,
            db=self.db,
            rate_limiter=self.rate_limiter,
            kb=self.kb,
            config=config,
        )
        logger.info("Message handler initialized")

        # ── MathCafe service ─────────────────────────────────────────────────
        from bot.services.mathcafe import MathCafeService
        self.mathcafe = MathCafeService(self)
        logger.info("MathCafe loaded: %d facts in queue", len(self.mathcafe.facts))

        # ── Telegram broadcaster (send-only, for channel announcements) ──────────
        self.telegram_connector = None
        if config.telegram_enabled and config.telegram_token:
            from bot.services.telegram_broadcaster import TelegramBroadcaster
            self.telegram_connector = TelegramBroadcaster(token=config.telegram_token)
            logger.info("Telegram broadcaster initialized (target: %s)", config.telegram_broadcast_target)

        # ── Load all extensions ──────────────────────────────────────────────
        for ext in EXTENSIONS:
            await self.load_extension(ext)
            logger.info("Loaded extension: %s", ext)

        if config.football_enabled:
            await self.load_extension("bot.commands.worldcup")
            logger.info("World Cup command loaded")
        else:
            logger.info("World Cup disabled — set FOOTBALL_ENABLED=true to enable")

        # ── Load chat handler (free-form conversation) ───────────────────────
        await self.load_extension("bot.commands.chat")
        logger.info("Chat handler loaded")

        # Sync slash commands
        if config.discord_guild_id:
            guild = discord.Object(id=config.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Slash commands synced to guild %d", config.discord_guild_id)
        else:
            await self.tree.sync()
            logger.info("Slash commands synced globally (may take up to 1 hour)")

    async def on_ready(self) -> None:
        assert self.user is not None
        logger.info("GSA Gateway ready — logged in as %s (ID: %d)", self.user, self.user.id)
        logger.info(
            "Knowledge base active: %d FAQ entries, %d contacts, %d events, %d resource categories",
            len(self.kb.faq_entries),
            len(self.kb.contacts),
            len(self.kb.events),
            len(self.kb.resources),
        )
        # RAG status
        if self.retriever:
            chunk_count = self.vector_store.get_chunk_count() if self.vector_store else 0
            logger.info("RAG pipeline active: %d chunks indexed", chunk_count)
        else:
            logger.info("RAG pipeline: disabled (no retriever)")
        if self.conversation_manager:
            logger.info("Conversation manager: active")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="/help  ·  GSA Gateway",
            )
        )

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ) -> None:
        logger.error("App command error: %s", error, exc_info=error)
        msg = (
            "Something went wrong processing that command. "
            "Please try again, or contact a GSA officer if the issue persists."
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass  # Interaction already expired

    async def close(self) -> None:
        if self.embedder:
            await self.embedder.close()
        if self.ollama:
            await self.ollama.close()
        football_client = getattr(self, "football_client", None)
        if football_client:
            await football_client.close()
        await super().close()


async def main() -> None:
    if not config.discord_token:
        logger.error(
            "DISCORD_TOKEN is not set. Copy .env.example → .env and add your token."
        )
        sys.exit(1)

    bot = GSABot()
    async with bot:
        await bot.start(config.discord_token)


if __name__ == "__main__":
    asyncio.run(main())
