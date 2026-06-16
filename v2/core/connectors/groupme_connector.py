"""GroupMeConnector — BaseConnector for dashboard/scheduler post delivery.

GroupMe renders plain text only (no markdown, no inline keyboards, no attachments via
the bot post API). Outbound delivery uses the bot ID only — see
``v2/integration/groupme_client.py``.

This is separate from ``bot/connectors/groupme_connector.py``, which handles
conversational Q&A polling in its own process.
"""

from __future__ import annotations

import re

from .base import BaseConnector, Button, DeliveryResult

_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")


def _strip_markdown(text: str) -> str:
    return _BOLD.sub(lambda m: m.group(1) or m.group(2), text)


class GroupMeConnector(BaseConnector):
    name = "groupme"

    def __init__(self, client=None, enabled: bool = True):
        self.client = client
        self.enabled = enabled

    def format_content(self, content: str, signature: str | None) -> str:
        body = _strip_markdown(content)
        if signature:
            body = f"{body}\n\n{_strip_markdown(signature)}"
        return body

    async def _send(self, channel, content, **kw) -> DeliveryResult:
        if self.client is None:
            return DeliveryResult(False, self.name, channel=channel,
                                  error="groupme client not wired")
        try:
            mid = await self.client.send_message(channel, content, **kw)
            return DeliveryResult(True, self.name, message_id=str(mid), channel=channel)
        except Exception as exc:  # noqa: BLE001
            return DeliveryResult(False, self.name, channel=channel, error=str(exc))

    async def send_text(self, content, channel, metadata=None):
        return await self._send(channel, content)

    async def send_media(self, content, media_path, channel, metadata=None):
        return DeliveryResult(False, self.name, channel=channel,
                              error="GroupMe bot posts do not support media attachments")

    async def send_interactive(self, content, buttons: list[Button], channel, metadata=None):
        return DeliveryResult(False, self.name, channel=channel,
                              error="GroupMe bot posts do not support interactive buttons")

    async def health_check(self) -> bool:
        if self.client is None:
            return False
        try:
            return await self.client.ping()
        except Exception:  # noqa: BLE001
            return False
