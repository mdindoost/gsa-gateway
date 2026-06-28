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
#   events/initiative/feedback/resources -> dashboard/website (later phases)
# All-conversational: /contact and /help are retired (answered via chat); only
# /qrcode (a generative tool, not a lookup) remains a slash command.
EXTENSIONS = [
    "bot.commands.qrcode_cmd",
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
        self.v2_fixtures_runner = None
        self._fixtures_conn = None
        self.v2_failure_digest_runner = None
        self._failure_digest_conn = None
        # Dashboard control plane — supervised child process (always-on backend)
        self.dashboard_proc = None

    async def setup_hook(self) -> None:
        """Initialise services and load extensions before on_ready fires."""
        from bot.services.database import Database
        from bot.services.knowledge_base import KnowledgeBase
        from bot.services.moderation import RateLimiter
        from bot.services.search import SearchService

        self.db = Database(config.database_path)
        self.db.connect()
        self.db.init_tables()
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

        # ── Dashboard control plane ───────────────────────────────────────────
        # Launch v2/local_server.py as a supervised child so the localhost
        # dashboard backend + its /api/* job runner is always-on with the bot —
        # no separate systemd unit for a clean install to authorize.
        if config.dashboard_server_enabled:
            await self._start_dashboard_server()

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

    async def _start_dashboard_server(self) -> None:
        """Spawn v2/local_server.py as a supervised child process."""
        script = Path(__file__).resolve().parent.parent / "v2" / "local_server.py"
        env = dict(os.environ, GSA_SERVER_PORT=str(config.dashboard_server_port))
        try:
            self.dashboard_proc = await asyncio.create_subprocess_exec(
                sys.executable, str(script), env=env)
            logger.info("Dashboard control server launched (pid %s, port %d)",
                        self.dashboard_proc.pid, config.dashboard_server_port)
        except Exception:  # noqa: BLE001 - never let this crash startup
            logger.exception("Failed to launch dashboard control server")
            self.dashboard_proc = None

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
        wc_fixtures = os.getenv("WC_FIXTURES_ENABLED", "false").lower() == "true"
        if v2_sched or v2_wc or wc_fixtures:
            from v2.core.connectors.registry import ConnectorRegistry
            from v2.core.connectors.discord_connector import DiscordConnector
            from v2.core.connectors.groupme_connector import GroupMeConnector as GroupMePublishConnector
            from v2.core.connectors.telegram_connector import TelegramConnector
            from v2.integration.discord_client import DiscordClientAdapter
            from v2.integration.groupme_client import GroupMePostClient
            from v2.integration.telegram_client import TelegramClientAdapter
            registry = ConnectorRegistry()
            registry.register(DiscordConnector(client=DiscordClientAdapter(self)))
            if self.telegram_connector:
                registry.register(TelegramConnector(
                    client=TelegramClientAdapter(self.telegram_connector)))
            if config.groupme_bot_id:
                registry.register(GroupMePublishConnector(
                    client=GroupMePostClient(config.groupme_bot_id)))

            if v2_sched and self.v2_scheduler_runner is None:
                from v2.integration.scheduler_runner import SchedulerRunner
                self.v2_scheduler_runner = SchedulerRunner(
                    config.operations_db_path, config.database_path, registry)
                await self.v2_scheduler_runner.start()
                logger.info("V2 Scheduler active (%d connector(s))",
                            len(registry.get_enabled()))

            if v2_wc and self.v2_worldcup_runner is None:
                key = os.getenv("FOOTBALL_API_KEY", "")
                provider = os.getenv("WC_PROVIDER", "espn").strip().lower()
                # The live watcher is provider-selected (WC_PROVIDER, default espn). ESPN needs
                # no API key; only the football-data kill-switch requires FOOTBALL_API_KEY.
                if provider == "football_data" and not key:
                    logger.warning("V2_WORLDCUP_ENABLED + WC_PROVIDER=football_data but "
                                   "FOOTBALL_API_KEY missing — skipping")
                else:
                    # make_watcher: schedule-driven active-set poller. Watches every
                    # simultaneously-live match at once; enqueues start/score/full-time posts
                    # the v2 scheduler delivers. Idle between game windows.
                    from v2.integration.wc_providers.watcher import make_watcher
                    chan = os.getenv("FOOTBALL_CHANNEL", "world-cup-2026")
                    org_slug = os.getenv("FOOTBALL_ORG_SLUG", "gsa")
                    self.v2_worldcup_runner = make_watcher(
                        key, config.operations_db_path, config.database_path,
                        org_slug, chan)
                    await self.v2_worldcup_runner.start()
                    logger.info("V2 World Cup watcher active (provider=%s, channel #%s)",
                                provider, chan)

            # Daily World Cup fixtures digest (a buffered-lane generator). It
            # enqueues posts that the v2 scheduler delivers, so it needs v2_sched.
            if wc_fixtures and self.v2_fixtures_runner is None:
                key = os.getenv("FOOTBALL_API_KEY", "")
                if not key:
                    logger.warning("WC_FIXTURES_ENABLED set but FOOTBALL_API_KEY missing — skipping")
                elif not v2_sched:
                    logger.warning("WC_FIXTURES_ENABLED set but V2_SCHEDULER_ENABLED is off — "
                                   "fixture digests would queue but never deliver; skipping")
                else:
                    from v2.core.database.schema import get_connection, get_ops_connection
                    from v2.core.publishing.sources import SourceRunner
                    from v2.integration.daily_fixtures import DailyFixturesSource
                    from v2.core.publishing.sources import platform_channels
                    from v2.core.publishing.org_resolve import resolve_org
                    chan = os.getenv("FOOTBALL_CHANNEL", "world-cup-2026")
                    org_slug = os.getenv("FOOTBALL_ORG_SLUG", "gsa")
                    hour = int(os.getenv("WC_FIXTURES_HOUR_ET", "9"))
                    # KB conn for org lookup (resolve_org enforces LOW-11); OPS conn for post writes
                    self._fixtures_kb_conn = get_connection(config.database_path)
                    self._fixtures_conn = get_ops_connection(config.operations_db_path)
                    try:
                        org_row = resolve_org(self._fixtures_kb_conn, org_slug)
                    except ValueError:
                        logger.warning("WC fixtures: org slug '%s' not found — skipping", org_slug)
                        self._fixtures_conn.close()
                        self._fixtures_conn = None
                        self._fixtures_kb_conn.close()
                        self._fixtures_kb_conn = None
                        org_row = None
                    if org_row is not None:
                        try:
                            source = DailyFixturesSource(
                                api_key=key, org_id=org_row["id"],
                                channels=platform_channels(), discord_channel=chan,
                                post_hour_et=hour)
                            self.v2_fixtures_runner = SourceRunner(
                                self._fixtures_conn, self._fixtures_kb_conn,
                                source, interval=3600)
                            await self.v2_fixtures_runner.start()
                            logger.info("V2 WC fixtures digest active (channel #%s, %02d:00 ET, hourly)",
                                        chan, hour)
                        except Exception:  # noqa: BLE001 - never let wiring crash startup
                            logger.exception("WC fixtures runner failed to start")
                            self._fixtures_conn.close()
                            self._fixtures_conn = None
                            self.v2_fixtures_runner = None

            # Active failure digest (accuracy backlog #3) — admin-only push of 👎 + low-confidence.
            failure_digest = os.getenv("FAILURE_DIGEST_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
            if failure_digest and self.v2_failure_digest_runner is None:
                chan = os.getenv("FAILURE_DIGEST_CHANNEL", "").strip()
                if not chan:
                    # R4 privacy guard: the digest contains user questions — refuse to start without an
                    # explicit admin channel (never fall back to default/student broadcast channels).
                    logger.warning("FAILURE_DIGEST_ENABLED set but FAILURE_DIGEST_CHANNEL unset — "
                                   "refusing to start (digest contains user questions; admin channel required)")
                elif not v2_sched:
                    logger.warning("FAILURE_DIGEST_ENABLED set but V2_SCHEDULER_ENABLED is off — "
                                   "digests would queue but never deliver; skipping")
                else:
                    from v2.core.database.schema import get_connection, get_ops_connection
                    from v2.core.publishing.sources import SourceRunner, platform_channels
                    from v2.core.publishing.org_resolve import resolve_org
                    from v2.integration.failure_digest import FailureDigestSource
                    org_slug = os.getenv("FAILURE_DIGEST_ORG_SLUG", "gsa")
                    hour = int(os.getenv("FAILURE_DIGEST_HOUR_ET", "9"))
                    period = int(os.getenv("FAILURE_DIGEST_PERIOD_DAYS", "1"))
                    plats = [p.strip() for p in os.getenv("FAILURE_DIGEST_PLATFORMS", "").split(",") if p.strip()]
                    self._failure_digest_kb_conn = get_connection(config.database_path)
                    self._failure_digest_conn = get_ops_connection(config.operations_db_path)
                    try:
                        org_row = resolve_org(self._failure_digest_kb_conn, org_slug)
                    except ValueError:
                        logger.warning("failure digest: org slug '%s' not found — skipping", org_slug)
                        self._failure_digest_conn.close()
                        self._failure_digest_conn = None
                        self._failure_digest_kb_conn.close()
                        self._failure_digest_kb_conn = None
                        org_row = None
                    if org_row is not None:
                        try:
                            source = FailureDigestSource(
                                self._failure_digest_kb_conn, org_id=org_row["id"],
                                channels=plats or platform_channels(), discord_channel=chan,
                                period_days=period, post_hour_et=hour)
                            self.v2_failure_digest_runner = SourceRunner(
                                self._failure_digest_conn, self._failure_digest_kb_conn,
                                source, interval=3600)
                            await self.v2_failure_digest_runner.start()
                            logger.info("V2 failure digest active (channel #%s, %02d:00 ET, every %dd)",
                                        chan, hour, period)
                        except Exception:  # noqa: BLE001 - never let wiring crash startup
                            logger.exception("failure digest runner failed to start")
                            self._failure_digest_conn.close()
                            self._failure_digest_conn = None
                            self._failure_digest_kb_conn.close()
                            self._failure_digest_kb_conn = None
                            self.v2_failure_digest_runner = None
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
        if self.dashboard_proc and self.dashboard_proc.returncode is None:
            self.dashboard_proc.terminate()
            try:
                await asyncio.wait_for(self.dashboard_proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.dashboard_proc.kill()
            logger.info("Dashboard control server stopped")
        if self.v2_fixtures_runner:
            await self.v2_fixtures_runner.stop()
        if self._fixtures_conn:
            self._fixtures_conn.close()
        if self.v2_failure_digest_runner:
            await self.v2_failure_digest_runner.stop()
        if self._failure_digest_conn:
            self._failure_digest_conn.close()
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
