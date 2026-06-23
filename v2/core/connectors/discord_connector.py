"""DiscordConnector — BaseConnector over Discord.

Transport is dependency-injected: ``client`` is any object exposing

    async send_message(channel, content, *, media_path=None, buttons=None) -> message_id
    async ping() -> bool

At integration time this wraps the running discord.py bot (resolving channel
names to channels and calling ``channel.send``). In tests a fake client is
injected. Discord renders markdown, so ``format_content`` passes content through
and appends the signature on a blank line.
"""

from __future__ import annotations

from .base import BaseConnector, Button, DeliveryResult


class DiscordConnector(BaseConnector):
    name = "discord"

    def __init__(self, client=None, enabled: bool = True):
        self.client = client
        self.enabled = enabled

    def format_content(self, content: str, signature: str | None) -> str:
        # Discord = markdown; keep as-is, append signature.
        return f"{content}\n\n{signature}" if signature else content

    async def _send(self, channel, content, **kw) -> DeliveryResult:
        if self.client is None:
            return DeliveryResult(False, self.name, channel=channel,
                                  error="discord client not wired")
        try:
            mid = await self.client.send_message(channel, content, **kw)
            return DeliveryResult(True, self.name, message_id=str(mid), channel=channel)
        except Exception as exc:  # noqa: BLE001
            return DeliveryResult(False, self.name, channel=channel, error=str(exc))

    async def delete_message(self, channel, message_id) -> DeliveryResult:
        if self.client is None:
            return DeliveryResult(False, self.name, channel=channel, message_id=message_id,
                                  error="discord client not wired")
        try:
            await self.client.delete_message(channel, message_id)
            return DeliveryResult(True, self.name, channel=channel, message_id=message_id)
        except Exception as exc:  # noqa: BLE001
            return DeliveryResult(False, self.name, channel=channel, message_id=message_id,
                                  error=str(exc))

    async def send_text(self, content, channel, metadata=None):
        return await self._send(channel, content)

    async def send_media(self, content, media_path, channel, metadata=None):
        return await self._send(channel, content, media_path=media_path)

    async def send_interactive(self, content, buttons: list[Button], channel, metadata=None):
        return await self._send(channel, content, buttons=buttons)

    async def health_check(self) -> bool:
        if self.client is None:
            return False
        try:
            return await self.client.ping()
        except Exception:  # noqa: BLE001
            return False
