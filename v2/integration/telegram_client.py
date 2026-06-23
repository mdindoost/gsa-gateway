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
        # Return the REAL (message_id, chat_id) so post_deliveries persists a deletable id
        # (scheduled-deletion Phase 0) — not the old "telegram-broadcast" sentinel.
        msg = await self.broadcaster.broadcast(content, parse_mode=parse_mode)
        if msg is None:
            raise RuntimeError("Telegram broadcast failed (no target or send error)")
        return (msg.message_id, msg.chat.id)

    async def delete_message(self, channel, message_id):
        # channel is the real chat_id Phase 0 stored. Lets telegram.error propagate so the
        # TelegramConnector can classify terminal vs transient.
        return await self.broadcaster.delete(channel, message_id)

    async def ping(self) -> bool:
        return self.broadcaster is not None
