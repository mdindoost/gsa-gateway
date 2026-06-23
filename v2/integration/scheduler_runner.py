"""SchedulerRunner — background polling loop around the v2 Scheduler.

Runs as an asyncio task on the bot's event loop. Every ``interval`` seconds it
calls ``Scheduler.tick()`` (materialize due templates/event-reminders, then
publish due posts via the ConnectorRegistry). It owns one sqlite connection,
created on the loop thread and reused across ticks. Knows nothing about
platforms — the registry does.
"""

from __future__ import annotations

import asyncio
import logging

from v2.core.database.schema import get_connection
from v2.core.publishing.publisher import PostPublisher
from v2.core.publishing.scheduler import Scheduler
from v2.core.publishing.signature import SignatureService

logger = logging.getLogger(__name__)


class SchedulerRunner:
    def __init__(self, db_path: str, registry, interval: int = 30):
        self.db_path = db_path
        self.registry = registry
        self.interval = interval
        self._conn = None
        self._scheduler = None
        self._task = None
        self._running = False

    async def start(self):
        self._conn = get_connection(self.db_path)   # loop thread
        self.registry.conn = self._conn             # so deliveries are logged
        publisher = PostPublisher(self._conn, self.registry, SignatureService(self._conn))
        self._scheduler = Scheduler(self._conn, publisher, registry=self.registry)
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        while self._running:
            try:
                result = await self._scheduler.tick()
                if result.get("published"):
                    logger.info("V2 scheduler tick: %s", result)
            except Exception:  # noqa: BLE001 - a bad tick must not kill the loop
                logger.exception("V2 scheduler tick failed")
            await asyncio.sleep(self.interval)

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._conn:
            self._conn.close()
        logger.info("V2 scheduler stopped")
