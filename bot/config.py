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
    ollama_base_url: str
    log_level: str
    allowed_channels: list[str]
    bot_prefix: str
    data_dir: Path


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
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        allowed_channels=allowed,
        bot_prefix=os.getenv("BOT_PREFIX", "gsa"),
        data_dir=Path(__file__).parent / "data",
    )


config = load_config()
