"""Connector abstraction (Pillar 6) — platform-independent message delivery.

Every outgoing message is a ``Post``. A ``BaseConnector`` knows how to format and
transmit a post for ONE platform; the ``ConnectorRegistry`` fans a post out to
all targeted connectors. Services and the scheduler never import a specific
platform — adding WhatsApp is one new ``BaseConnector`` subclass and a
``register()`` call, nothing else.

This is a different concern from v1's ``bot/connectors/BasePlatform`` (which is a
bot *lifecycle* interface: start/stop/setup_services). These send-oriented
connectors coexist with it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC, matches the rest of v2


@dataclass
class DeliveryResult:
    success: bool
    platform: str
    message_id: str | None = None
    channel: str | None = None
    error: str | None = None
    sent_at: datetime = field(default_factory=_utcnow)

    @property
    def status(self) -> str:
        """Maps to the post_deliveries.status CHECK set."""
        return "success" if self.success else "failed"


@dataclass
class Button:
    label: str
    callback_data: str
    emoji: str | None = None
    style: str = "secondary"  # primary | secondary | danger


@dataclass
class Post:
    """The connector-layer view of an outgoing message.

    The publisher (Step 6) builds this from a ``posts`` table row + settings.
    ``platform_channels`` maps a platform name to its target channel; ``channels``
    lists the platforms this post is intended for.
    """

    content: str
    channels: list[str] = field(default_factory=list)
    id: int | None = None
    signature: str | None = None
    media_path: str | None = None
    buttons: list[Button] = field(default_factory=list)
    platform_channels: dict[str, str] = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def channel_for(self, platform: str) -> str | None:
        return self.platform_channels.get(platform)


class BaseConnector(ABC):
    """One platform's delivery interface. Subclasses must not raise from sends —
    they return a ``DeliveryResult`` (the registry double-guards regardless)."""

    name: str = "base"
    enabled: bool = True

    @abstractmethod
    async def send_text(self, content: str, channel: str | None,
                        metadata: dict | None = None) -> DeliveryResult: ...

    @abstractmethod
    async def send_media(self, content: str, media_path: str, channel: str | None,
                         metadata: dict | None = None) -> DeliveryResult: ...

    @abstractmethod
    async def send_interactive(self, content: str, buttons: list[Button],
                               channel: str | None,
                               metadata: dict | None = None) -> DeliveryResult: ...

    @abstractmethod
    async def health_check(self) -> bool: ...

    async def delete_message(self, channel: str | None, message_id: str) -> DeliveryResult:
        """Unsend a previously delivered message. Default: unsupported (send-only platforms
        like GroupMe inherit this). Platforms that CAN delete override it. Never raises —
        returns a DeliveryResult; the deleter maps it to a per-delivery delete_status."""
        return DeliveryResult(False, self.name, channel=channel, message_id=message_id,
                              error="delete unsupported")

    def format_content(self, content: str, signature: str | None) -> str:
        """Platform-specific formatting. Default: append the signature on a blank
        line. Discord/Telegram override for markdown vs HTML."""
        return f"{content}\n\n{signature}" if signature else content
