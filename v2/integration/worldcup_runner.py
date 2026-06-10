"""WorldCupRunner — background poll loop that turns live match events into posts.

Runs as an asyncio task on the bot's event loop. Every ``interval`` seconds it
asks the WorldCupTracker for NEW events and enqueues each one as a ``posts`` row
via ``enqueue_post`` (the standard generator contract). The live SchedulerRunner
then delivers those rows through the ConnectorRegistry (→ Discord + Telegram).

This is the canonical example of a content generator on the buffered lane: it
owns only the trigger + data-fetch; validation, persistence and dispatch are the
system's job. A bad tick never kills the loop.
"""
from __future__ import annotations

import asyncio
import logging

from v2.core.database.schema import get_connection
from v2.core.publishing.sources import PostDraft, EnqueueError, enqueue_post
from v2.integration.worldcup_tracker import WorldCupTracker, format_event

logger = logging.getLogger(__name__)


class WorldCupRunner:
    def __init__(self, registry, api_key: str, channel: str, db_path: str,
                 org_slug: str = "gsa", interval: int = 60):
        self.registry = registry          # used only to validate channel names
        self.tracker = WorldCupTracker(api_key)
        self.channel = channel            # Discord channel name (Telegram via org settings)
        self.db_path = db_path
        self.org_slug = org_slug
        self.interval = interval
        self._conn = None
        self.org_id = None
        self.allowed = {"discord", "telegram"}
        self._task = None
        self._running = False

    async def start(self):
        self._conn = get_connection(self.db_path)   # own connection, on the loop thread
        row = self._conn.execute(
            "SELECT id FROM organizations WHERE slug=?", (self.org_slug,)
        ).fetchone()
        if row is None:
            raise RuntimeError(f"World Cup: org slug '{self.org_slug}' not found")
        self.org_id = row["id"]
        if self.registry is not None:
            names = {c.name for c in self.registry.get_enabled()}
            if names:
                self.allowed = names
        self._running = True
        ok = await self.tracker.health_check()
        logger.info("V2 World Cup tracker started (feed reachable: %s, channel #%s, org=%s, %ds)",
                    ok, self.channel, self.org_id, self.interval)
        self._task = asyncio.create_task(self._loop())

    async def _loop_once(self) -> int:
        """One poll → enqueue cycle. Returns how many posts were enqueued."""
        events = await self.tracker.check_matches()
        enqueued = 0
        for ev in events:
            # Explicit, semantic dedup key (per match + event), so dedup is not a
            # content coincidence. Field names verified against worldcup_tracker.py:
            # ev["match"]["id"] exists on all event types; ev.get("minute") exists
            # on goal events and is absent on kickoff/halftime/etc (defaults to "").
            match_id = (ev.get("match") or {}).get("id")
            dedup_key = f"{match_id}:{ev.get('type')}:{ev.get('minute', '')}"
            draft = PostDraft(
                org_id=self.org_id,
                content=format_event(ev),
                type="worldcup",
                channels=["discord", "telegram"],
                discord_channel=self.channel,
                source_type="worldcup",
                dedup_key=dedup_key,
                metadata={"event_type": ev.get("type")},
            )
            try:
                enqueue_post(self._conn, draft, allowed_channels=self.allowed)
                enqueued += 1
            except EnqueueError as exc:
                logger.warning("World Cup: dropped invalid event draft: %s", exc)
        if enqueued:
            logger.info("V2 World Cup: enqueued %d post(s)", enqueued)
        return enqueued

    async def _loop(self):
        while self._running:
            try:
                await self._loop_once()
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
        if self._conn:
            self._conn.close()
        logger.info("V2 World Cup tracker stopped")
