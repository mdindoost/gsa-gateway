"""Rate limiting and channel allowlist enforcement."""

import logging
from collections import defaultdict
from datetime import datetime, timedelta

import discord

logger = logging.getLogger(__name__)


class RateLimiter:
    """Sliding-window in-memory rate limiter (resets on bot restart)."""

    def __init__(self, max_calls: int = 5, period_seconds: int = 60) -> None:
        self.max_calls = max_calls
        self.period = timedelta(seconds=period_seconds)
        self._calls: dict[int, list[datetime]] = defaultdict(list)

    def is_allowed(self, user_id: int) -> bool:
        """Return True if this user has not exceeded the rate limit."""
        now = datetime.utcnow()
        window = self._calls[user_id]
        # Evict timestamps outside the rolling window
        window[:] = [t for t in window if now - t < self.period]
        if len(window) >= self.max_calls:
            return False
        window.append(now)
        return True

    def get_retry_after(self, user_id: int) -> float:
        """Return seconds until the oldest call expires from the window."""
        calls = self._calls.get(user_id, [])
        if not calls:
            return 0.0
        oldest = min(calls)
        return max(0.0, (oldest + self.period - datetime.utcnow()).total_seconds())


def is_channel_allowed(
    channel: discord.abc.GuildChannel | discord.DMChannel | None,
    allowed_channels: list[str],
) -> bool:
    """Return True when the channel is in the allowlist (or the list is empty)."""
    if not allowed_channels:
        return True
    if channel is None:
        return False
    name = getattr(channel, "name", "")
    return name in allowed_channels


def is_admin(interaction: discord.Interaction, admin_role_name: str) -> bool:
    """Return True if the interaction user holds the admin role."""
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    return any(role.name == admin_role_name for role in member.roles)
