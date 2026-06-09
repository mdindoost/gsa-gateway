"""TelegramClientAdapter — transport client for the v2 TelegramConnector.

Wraps the existing send-only ``TelegramBroadcaster`` (already running in the
Discord process). The broadcaster sends to its single configured target, so the
``channel`` argument is informational only.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class TelegramClientAdapter:
    def __init__(self, broadcaster):
        self.broadcaster = broadcaster

    async def send_message(self, channel, content, parse_mode="HTML", media_path=None, **kw):
        # TelegramConnector already produced HTML; broadcast to the configured target.
        ok = await self.broadcaster.broadcast(content, parse_mode=parse_mode)
        if not ok:
            raise RuntimeError("Telegram broadcast failed (no target or send error)")
        return "telegram-broadcast"

    async def ping(self) -> bool:
        return self.broadcaster is not None
