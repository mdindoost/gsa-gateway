"""StubConnector — test-only connector. Records every call, sends nothing."""

from __future__ import annotations

from .base import BaseConnector, Button, DeliveryResult


class StubConnector(BaseConnector):
    def __init__(self, name: str = "stub", enabled: bool = True,
                 healthy: bool = True, fail: bool = False):
        self.name = name
        self.enabled = enabled
        self._healthy = healthy
        self._fail = fail            # when True, sends return a failed result
        self.calls: list[dict] = []  # inspect in tests
        self._counter = 0

    def _record(self, kind: str, channel: str | None, **extra) -> DeliveryResult:
        self.calls.append({"kind": kind, "channel": channel, **extra})
        if self._fail:
            return DeliveryResult(False, self.name, channel=channel, error="stub failure")
        self._counter += 1
        return DeliveryResult(True, self.name, message_id=f"{self.name}-{self._counter}",
                              channel=channel)

    async def send_text(self, content, channel, metadata=None):
        return self._record("text", channel, content=content, metadata=metadata)

    async def send_media(self, content, media_path, channel, metadata=None):
        return self._record("media", channel, content=content, media_path=media_path,
                            metadata=metadata)

    async def send_interactive(self, content, buttons: list[Button], channel, metadata=None):
        return self._record("interactive", channel, content=content,
                            buttons=buttons, metadata=metadata)

    async def health_check(self) -> bool:
        return self._healthy
