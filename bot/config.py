"""Central configuration loaded from environment variables."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    """Typed configuration object for the GSA Gateway bot."""

    discord_token: str
    discord_guild_id: int | None
    admin_role_name: str
    database_path: str
    ollama_enabled: bool
    ollama_model: str
    ollama_url: str
    ollama_timeout: int
    log_level: str
    allowed_channels: list[str]
    bot_prefix: str
    data_dir: Path
    # Announcement channel names (must match Discord channel names exactly)
    channel_announcements: str
    channel_events: str
    channel_food: str
    channel_funding: str
    channel_wellness: str
    channel_research: str
    channel_international: str
    # Scheduler settings
    daily_digest_hour: int
    daily_digest_minute: int
    reminder_check_interval: int
    # RAG / vector store settings
    chroma_db_path: str
    conversation_timeout_minutes: int
    conversation_max_turns: int
    embedding_model: str
    ask_gsa_channel: str
    # MathCafe
    mathcafe_channel: str
    # Admin notification
    admin_discord_id: int | None
    # Telegram
    telegram_token: str


def load_config() -> Config:
    """Read environment variables and return a validated Config object."""
    raw_guild = os.getenv("DISCORD_GUILD_ID", "").strip()
    guild_id = int(raw_guild) if raw_guild else None

    raw_channels = os.getenv("ALLOWED_CHANNELS", "").strip()
    allowed = [ch.strip() for ch in raw_channels.split(",") if ch.strip()]

    return Config(
        discord_token=os.getenv("DISCORD_TOKEN", ""),
        discord_guild_id=guild_id,
        admin_role_name=os.getenv("ADMIN_ROLE_NAME", "GSA Officer"),
        database_path=os.getenv("DATABASE_PATH", "./gsa_gateway.db"),
        ollama_enabled=os.getenv("OLLAMA_ENABLED", "false").lower() == "true",
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
        ollama_url=os.getenv("OLLAMA_URL", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")),
        ollama_timeout=int(os.getenv("OLLAMA_TIMEOUT", "60")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        allowed_channels=allowed,
        bot_prefix=os.getenv("BOT_PREFIX", "gsa"),
        data_dir=Path(__file__).parent / "data",
        channel_announcements=os.getenv("CHANNEL_ANNOUNCEMENTS", "gsa-announcements"),
        channel_events=os.getenv("CHANNEL_EVENTS", "gsa-events"),
        channel_food=os.getenv("CHANNEL_FOOD", "gsa-food"),
        channel_funding=os.getenv("CHANNEL_FUNDING", "gsa-funding"),
        channel_wellness=os.getenv("CHANNEL_WELLNESS", "gsa-wellness"),
        channel_research=os.getenv("CHANNEL_RESEARCH", "gsa-research"),
        channel_international=os.getenv("CHANNEL_INTERNATIONAL", "gsa-international"),
        daily_digest_hour=int(os.getenv("DAILY_DIGEST_HOUR", "9")),
        daily_digest_minute=int(os.getenv("DAILY_DIGEST_MINUTE", "0")),
        reminder_check_interval=int(os.getenv("REMINDER_CHECK_INTERVAL", "30")),
        chroma_db_path=os.getenv("CHROMA_DB_PATH", "./chroma_db"),
        conversation_timeout_minutes=int(os.getenv("CONVERSATION_TIMEOUT_MINUTES", "60")),
        conversation_max_turns=int(os.getenv("CONVERSATION_MAX_TURNS", "5")),
        embedding_model=os.getenv("EMBEDDING_MODEL", "nomic-embed-text"),
        ask_gsa_channel=os.getenv("ASK_GSA_CHANNEL", "ask-gsa"),
        mathcafe_channel=os.getenv("MATHCAFE_CHANNEL", "gsa-mathcafe"),
        admin_discord_id=int(raw_admin) if (raw_admin := os.getenv("ADMIN_DISCORD_ID", "").strip()) else None,
        telegram_token=os.getenv("TELEGRAM_TOKEN", ""),
    )


config = load_config()
