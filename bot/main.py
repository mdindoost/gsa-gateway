"""GSA Gateway bot entry point — loads all cogs and starts the bot."""

import asyncio
import logging
import sys
from pathlib import Path

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
        # Shared services — set in setup_hook, typed here for IDE support
        self.db = None
        self.kb = None
        self.search_svc = None
        self.rate_limiter = None
        self.ollama = None

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

        self.kb = KnowledgeBase(data_dir=config.data_dir)
        self.kb.load()

        self.search_svc = SearchService(self.kb)
        self.rate_limiter = RateLimiter(max_calls=5, period_seconds=60)

        if config.ollama_enabled:
            from bot.services.ollama_client import OllamaClient

            self.ollama = OllamaClient(
                model=config.ollama_model,
                base_url=config.ollama_url,
                timeout=config.ollama_timeout,
            )
            logger.info("Ollama client initialised (model=%s)", config.ollama_model)
            await self.ollama.check_connection()

        for ext in EXTENSIONS:
            await self.load_extension(ext)
            logger.info("Loaded extension: %s", ext)

        # Sync to guild for instant propagation during development,
        # or globally when no guild ID is configured.
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
