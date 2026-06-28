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

from v2.core.database.schema import get_connection, get_ops_connection
from v2.core.publishing.publisher import PostPublisher
from v2.core.publishing.scheduler import Scheduler
from v2.core.publishing.signature import SignatureService

logger = logging.getLogger(__name__)


class SchedulerRunner:
    def __init__(self, ops_path: str, kb_path: str, registry, interval: int = 30):
        """Two-connection constructor.

        ``ops_path`` — path to the OPS DB (posts, deliveries, templates, events).
        ``kb_path``  — path to the Knowledge DB (settings, org reads).

        For the behavior-preserving combined-file mode, pass the same path for
        both: ``SchedulerRunner(db_path, db_path, registry)``.
        """
        self.ops_path = ops_path
        self.kb_path = kb_path
        self.registry = registry
        self.interval = interval
        self._ops_conn = None
        self._kb_conn = None
        self._scheduler = None
        self._task = None
        self._running = False

    async def start(self):
        self._ops_conn = get_ops_connection(self.ops_path)   # OPS: posts / deliveries
        self._kb_conn = get_connection(self.kb_path)         # Knowledge: settings / orgs
        self.registry.conn = self._ops_conn                  # deliveries logged on OPS
        sigs = SignatureService(self._kb_conn)
        publisher = PostPublisher(self._ops_conn, self._kb_conn, self.registry, sigs)
        self._scheduler = Scheduler(self._ops_conn, self._kb_conn, publisher,
                                    registry=self.registry)
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
        if self._ops_conn:
            self._ops_conn.close()
        if self._kb_conn:
            self._kb_conn.close()
        logger.info("V2 scheduler stopped")
