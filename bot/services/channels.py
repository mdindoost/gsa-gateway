"""Channel routing — maps event categories to Discord text channels."""

import logging
from typing import Optional

import discord

from bot.config import config

logger = logging.getLogger(__name__)

# Maps event category tags to config attribute names
_CATEGORY_CONFIG_ATTR: dict[str, str] = {
    "events":        "channel_events",
    "food":          "channel_food",
    "funding":       "channel_funding",
    "wellness":      "channel_wellness",
    "research":      "channel_research",
    "international": "channel_international",
    "social":        "channel_events",
    "academic":      "channel_events",
    "other":         "channel_announcements",
    "general":       "channel_events",
}


def _find_channel(
    guild: discord.Guild, name: str
) -> Optional[discord.TextChannel]:
    ch = discord.utils.get(guild.text_channels, name=name)
    if ch is None:
        logger.warning(
            "Channel '%s' not found in guild '%s' — check CHANNEL_* in .env",
            name,
            guild.name,
        )
    return ch


def get_channel_for_category(
    guild: discord.Guild,
    category: str,
) -> Optional[discord.TextChannel]:
    """Return the best Discord channel for a category tag, with fallback.

    Falls back to the announcements channel if the specific channel is missing.
    Returns None (with a logged warning) only if neither channel exists.
    """
    tag = category.lower().strip()
    attr = _CATEGORY_CONFIG_ATTR.get(tag, "channel_events")
    channel_name: str = getattr(config, attr, config.channel_events)

    ch = _find_channel(guild, channel_name)
    if ch is not None:
        return ch

    # Fall back to announcements channel
    fallback = _find_channel(guild, config.channel_announcements)
    if fallback is None:
        logger.warning(
            "Neither '%s' nor '%s' exists in guild — announcement skipped",
            channel_name,
            config.channel_announcements,
        )
    return fallback


def get_announcement_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """Return the main announcements channel for the guild."""
    return _find_channel(guild, config.channel_announcements)


def get_channels_for_categories(
    guild: discord.Guild,
    categories: list[str],
) -> list[discord.TextChannel]:
    """Return a de-duplicated list of channels for multiple category tags."""
    seen: set[int] = set()
    result: list[discord.TextChannel] = []
    for cat in categories:
        ch = get_channel_for_category(guild, cat)
        if ch is not None and ch.id not in seen:
            seen.add(ch.id)
            result.append(ch)
    return result
