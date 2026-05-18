"""Tests for admin role check and rate limiting logic."""

import time
from unittest.mock import MagicMock

import discord
import pytest

from bot.services.moderation import RateLimiter, is_admin, is_channel_allowed


# ── Admin role check ──────────────────────────────────────────────────────────

class TestAdminCheck:
    def _make_interaction(self, role_names: list[str]) -> MagicMock:
        """Build a mock interaction where the user holds the given roles."""
        interaction = MagicMock(spec=discord.Interaction)
        member = MagicMock(spec=discord.Member)
        roles = []
        for name in role_names:
            role = MagicMock(spec=discord.Role)
            role.name = name
            roles.append(role)
        member.roles = roles
        interaction.user = member
        return interaction

    def test_user_with_admin_role_passes(self) -> None:
        interaction = self._make_interaction(["Student", "GSA Officer"])
        assert is_admin(interaction, "GSA Officer") is True

    def test_user_without_admin_role_fails(self) -> None:
        interaction = self._make_interaction(["Student"])
        assert is_admin(interaction, "GSA Officer") is False

    def test_user_with_no_roles_fails(self) -> None:
        interaction = self._make_interaction([])
        assert is_admin(interaction, "GSA Officer") is False

    def test_role_name_is_case_sensitive(self) -> None:
        interaction = self._make_interaction(["gsa officer"])
        assert is_admin(interaction, "GSA Officer") is False

    def test_non_member_user_fails(self) -> None:
        """DM interactions where user is not a Member should fail gracefully."""
        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = MagicMock(spec=discord.User)  # not discord.Member
        assert is_admin(interaction, "GSA Officer") is False

    def test_multiple_roles_checks_all(self) -> None:
        interaction = self._make_interaction(["Role A", "Role B", "GSA Officer", "Role C"])
        assert is_admin(interaction, "GSA Officer") is True


# ── Rate limiter ──────────────────────────────────────────────────────────────

class TestRateLimiter:
    def test_first_call_allowed(self) -> None:
        limiter = RateLimiter(max_calls=5, period_seconds=60)
        assert limiter.is_allowed(1) is True

    def test_calls_within_limit_allowed(self) -> None:
        limiter = RateLimiter(max_calls=3, period_seconds=60)
        for _ in range(3):
            assert limiter.is_allowed(1) is True

    def test_exceeding_limit_denied(self) -> None:
        limiter = RateLimiter(max_calls=3, period_seconds=60)
        for _ in range(3):
            limiter.is_allowed(1)
        assert limiter.is_allowed(1) is False

    def test_different_users_are_independent(self) -> None:
        limiter = RateLimiter(max_calls=2, period_seconds=60)
        limiter.is_allowed(1)
        limiter.is_allowed(1)
        limiter.is_allowed(1)  # User 1 is over limit
        assert limiter.is_allowed(2) is True  # User 2 is unaffected

    def test_retry_after_is_positive_when_limited(self) -> None:
        limiter = RateLimiter(max_calls=1, period_seconds=60)
        limiter.is_allowed(5)
        limiter.is_allowed(5)  # Now limited
        retry = limiter.get_retry_after(5)
        assert retry > 0
        assert retry <= 60

    def test_retry_after_zero_when_not_limited(self) -> None:
        limiter = RateLimiter(max_calls=5, period_seconds=60)
        assert limiter.get_retry_after(99) == 0.0

    def test_window_expiry_allows_again(self) -> None:
        """After the period, calls should be allowed again."""
        limiter = RateLimiter(max_calls=1, period_seconds=1)
        limiter.is_allowed(7)
        limiter.is_allowed(7)  # Denied
        assert limiter.is_allowed(7) is False

        # Simulate 2-second wait by backdating the stored timestamp
        from datetime import datetime, timedelta, timezone
        limiter._calls[7] = [datetime.now(timezone.utc) - timedelta(seconds=2)]
        assert limiter.is_allowed(7) is True  # Window cleared


# ── Channel allowlist ─────────────────────────────────────────────────────────

class TestChannelAllowlist:
    def _make_channel(self, name: str):
        ch = MagicMock()
        ch.name = name
        return ch

    def test_empty_allowlist_permits_all(self) -> None:
        ch = self._make_channel("random-channel")
        assert is_channel_allowed(ch, []) is True

    def test_allowed_channel_passes(self) -> None:
        ch = self._make_channel("gsa-general")
        assert is_channel_allowed(ch, ["gsa-general", "gsa-events"]) is True

    def test_unlisted_channel_blocked(self) -> None:
        ch = self._make_channel("off-topic")
        assert is_channel_allowed(ch, ["gsa-general"]) is False

    def test_none_channel_blocked_when_list_nonempty(self) -> None:
        assert is_channel_allowed(None, ["gsa-general"]) is False

    def test_none_channel_allowed_when_list_empty(self) -> None:
        assert is_channel_allowed(None, []) is True
