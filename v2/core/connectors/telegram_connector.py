"""TelegramConnector — BaseConnector over Telegram.

Transport is dependency-injected like the Discord one: ``client`` exposes

    async send_message(channel, content, *, parse_mode='HTML', media_path=None) -> message_id
    async ping() -> bool

Telegram renders HTML, so ``format_content`` converts the markdown we store
(``**bold**``/``__bold__``, ``*italic*``/``_italic_``) into ``<b>``/``<i>`` tags
and HTML-escapes the rest, then appends the signature.
"""

from __future__ import annotations

import html
import re

from .base import BaseConnector, Button, DeliveryResult

_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", re.DOTALL)


def markdown_to_html(text: str) -> str:
    """Minimal, safe markdown -> Telegram HTML for bold/italic."""
    text = html.escape(text)
    text = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)
    return text


class TelegramConnector(BaseConnector):
    name = "telegram"

    def __init__(self, client=None, enabled: bool = True):
        self.client = client
        self.enabled = enabled

    def format_content(self, content: str, signature: str | None) -> str:
        body = markdown_to_html(content)
        if signature:
            body = f"{body}\n\n{markdown_to_html(signature)}"
        return body

    async def _send(self, channel, content, **kw) -> DeliveryResult:
        if self.client is None:
            return DeliveryResult(False, self.name, channel=channel,
                                  error="telegram client not wired")
        try:
            mid = await self.client.send_message(channel, content, parse_mode="HTML", **kw)
            return DeliveryResult(True, self.name, message_id=str(mid), channel=channel)
        except Exception as exc:  # noqa: BLE001
            return DeliveryResult(False, self.name, channel=channel, error=str(exc))

    async def send_text(self, content, channel, metadata=None):
        return await self._send(channel, content)

    async def send_media(self, content, media_path, channel, metadata=None):
        return await self._send(channel, content, media_path=media_path)

    async def send_interactive(self, content, buttons: list[Button], channel, metadata=None):
        # Telegram inline keyboards are passed through to the client adapter.
        return await self._send(channel, content, buttons=buttons)

    async def health_check(self) -> bool:
        if self.client is None:
            return False
        try:
            return await self.client.ping()
        except Exception:  # noqa: BLE001
            return False
