"""WorldCupRunner — background poll loop that publishes match events via v2.

Runs as an asyncio task on the bot's event loop. Every ``interval`` seconds it
asks the WorldCupTracker for new events and publishes each one through the shared
ConnectorRegistry (→ Discord + Telegram). Knows nothing about platforms.

Events are sent directly (no posts row) for low latency; the connectors log each
send. A bad tick never kills the loop.
"""

from __future__ import annotations

import asyncio
import logging

from v2.core.connectors.base import Post
from v2.integration.worldcup_tracker import WorldCupTracker, format_event

logger = logging.getLogger(__name__)


class WorldCupRunner:
    def __init__(self, registry, api_key: str, channel: str, interval: int = 60):
        self.registry = registry
        self.tracker = WorldCupTracker(api_key)
        self.channel = channel          # Discord channel name (Telegram broadcasts)
        self.interval = interval
        self._task = None
        self._running = False

    async def start(self):
        self._running = True
        # one immediate health check so a broken key/feed is visible at startup
        ok = await self.tracker.health_check()
        logger.info("V2 World Cup tracker started (feed reachable: %s, channel #%s, %ds)",
                    ok, self.channel, self.interval)
        self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        while self._running:
            try:
                events = await self.tracker.check_matches()
                for ev in events:
                    post = Post(content=format_event(ev),
                                channels=["discord", "telegram"],
                                platform_channels={"discord": self.channel})
                    await self.registry.publish(post)
                if events:
                    logger.info("V2 World Cup: published %d event(s): %s",
                                len(events), [e["type"] for e in events])
            except Exception:  # noqa: BLE001 - a bad tick must not kill the loop
                logger.exception("V2 World Cup tick failed")
            await asyncio.sleep(self.interval)

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("V2 World Cup tracker stopped")
