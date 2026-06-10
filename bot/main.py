"""GSA Gateway bot entry point — loads all cogs and starts the bot."""

import asyncio
import logging
import os
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

# v2 platform cut (2026-06-10): the dashboard/DB + v2 scheduler is the sole
# originator of outbound messages. v1 keeps only the minimal built-in commands;
# the RAG `#ask-gsa` chat handler is loaded separately below. Removed:
#   ask        -> replaced by #ask-gsa RAG
#   admin      -> control moves to the v2 dashboard
#   scheduler  -> v2 scheduler (Wire B) owns all autonomous posting
#   events/initiative/feedback/resources/qrcode -> dashboard/website (later phases)
EXTENSIONS = [
    "bot.commands.contact",
    "bot.commands.help_cmd",
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
        self.telegram_connector = None
        # Message handler
        self.message_handler = None
        # V2 publishing (Wire B) — started in on_ready when enabled
        self.v2_scheduler_runner = None
        self.v2_worldcup_runner = None

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

        # ── Assistant brain (shared with the Telegram process via build_assistant) ──
        # One definition of retriever + LLM + conversation + handler, so the two
        # platforms can never drift. See docs/designs/2026-06-09-unified-assistant.md
        from bot.core.assistant import build_assistant
        asst = await build_assistant(config, self.db, self.kb, self.rate_limiter)
        self.ollama = asst.ollama
        self.embedder = asst.embedder
        self.vector_store = asst.vector_store
        self.conversation_manager = asst.conversation_manager
        self.intent_detector = asst.intent_detector
        self.retriever = asst.retriever
        self.message_handler = asst.message_handler
        logger.info("Assistant wired (Discord)")

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

        # WIRE B: start the v2 scheduler once ready (guilds populated for Discord).
        # Independent of v1's scheduler cog — only sends posts from the posts table.
        v2_sched = os.getenv("V2_SCHEDULER_ENABLED", "false").lower() == "true"
        v2_wc = os.getenv("V2_WORLDCUP_ENABLED", "false").lower() == "true"
        if v2_sched or v2_wc:
            from v2.core.connectors.registry import ConnectorRegistry
            from v2.core.connectors.discord_connector import DiscordConnector
            from v2.core.connectors.telegram_connector import TelegramConnector
            from v2.integration.discord_client import DiscordClientAdapter
            from v2.integration.telegram_client import TelegramClientAdapter
            registry = ConnectorRegistry()
            registry.register(DiscordConnector(client=DiscordClientAdapter(self)))
            if self.telegram_connector:
                registry.register(TelegramConnector(
                    client=TelegramClientAdapter(self.telegram_connector)))

            if v2_sched and self.v2_scheduler_runner is None:
                from v2.integration.scheduler_runner import SchedulerRunner
                self.v2_scheduler_runner = SchedulerRunner("gsa_gateway.db", registry)
                await self.v2_scheduler_runner.start()
                logger.info("V2 Scheduler active (%d connector(s))",
                            len(registry.get_enabled()))

            if v2_wc and self.v2_worldcup_runner is None:
                key = os.getenv("FOOTBALL_API_KEY", "")
                if key:
                    from v2.integration.worldcup_runner import WorldCupRunner
                    chan = os.getenv("FOOTBALL_CHANNEL", "world-cup-2026")
                    interval = int(os.getenv("FOOTBALL_POLL_INTERVAL", "60"))
                    org_slug = os.getenv("FOOTBALL_ORG_SLUG", "gsa")
                    self.v2_worldcup_runner = WorldCupRunner(
                        registry, key, chan, "gsa_gateway.db", org_slug, interval)
                    await self.v2_worldcup_runner.start()
                    logger.info("V2 World Cup active (channel #%s)", chan)
                else:
                    logger.warning("V2_WORLDCUP_ENABLED set but FOOTBALL_API_KEY missing — skipping")
        else:
            logger.info("V2 Scheduler disabled (default)")

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
        if self.v2_worldcup_runner:
            await self.v2_worldcup_runner.stop()
        if self.v2_scheduler_runner:
            await self.v2_scheduler_runner.stop()
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
