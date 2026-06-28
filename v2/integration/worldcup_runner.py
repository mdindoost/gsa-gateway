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
import os

from v2.core.database.schema import get_connection
from v2.core.publishing.sources import PostDraft, EnqueueError, enqueue_post, platform_channels
from v2.integration.worldcup_tracker import WorldCupTracker, format_event, format_standings

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
        # On a group-stage kick-off, append that group's table to the kick-off post
        # (one combined post). Default on; FOOTBALL_KICKOFF_STANDINGS=false disables.
        self.kickoff_standings = os.getenv("FOOTBALL_KICKOFF_STANDINGS", "true").lower() != "false"
        self._conn = None
        self.org_id = None
        self.allowed = set(platform_channels())
        self._task = None
        self._running = False

    async def start(self):
        self._conn = get_connection(self.db_path)   # own connection, on the loop thread
        try:
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
        except Exception:
            self._conn.close()
            self._conn = None
            raise
        self._running = True
        ok = await self.tracker.health_check()
        logger.info("V2 World Cup tracker started (feed reachable: %s, channel #%s, org=%s, %ds)",
                    ok, self.channel, self.org_id, self.interval)
        self._task = asyncio.create_task(self._loop())

    async def _loop_once(self) -> int:
        """One poll → enqueue cycle. Returns how many posts were enqueued."""
        events = await self.tracker.check_matches()
        enqueued = 0
        seen_keys: dict[str, int] = {}
        for ev in events:
            # Explicit, semantic dedup key (per match + event). Goals carry NO
            # minute on the free tier, so a plain "id:goal:" key makes every goal
            # collide — the 2nd goal would dedup against the 1st across ticks and
            # never post. Use the scoreline (monotonic, unique per goal) as the
            # discriminator for goals; the real minute when present (paid tier).
            match = ev.get("match") or {}
            match_id = match.get("id")
            ev_type = ev.get("type")
            if ev_type == "goal":
                ft = match.get("score", {}).get("fullTime", {})
                disc = ev.get("minute") or f"{ft.get('home') or 0}-{ft.get('away') or 0}"
            else:
                disc = ev.get("minute", "")
            base_key = f"{match_id}:{ev_type}:{disc}"
            seen_keys[base_key] = seen_keys.get(base_key, 0) + 1
            # disambiguate same-key events within one tick (e.g. two goals that
            # land on the same scoreline in a single poll) so none are dropped
            dedup_key = base_key if seen_keys[base_key] == 1 else f"{base_key}#{seen_keys[base_key]}"
            # On a group-stage kick-off, append that group's table to the SAME post.
            # Failure-isolated: any standings error degrades to the plain kick-off
            # post (content is assigned before the try); knockout matches (no group)
            # and the toggle-off case fall through untouched.
            content = format_event(ev)
            if ev_type == "kickoff" and match.get("group") and self.kickoff_standings:
                try:
                    table = (await self.tracker.fetch_standings()).get(match["group"])
                    if table:
                        content = content + "\n\n" + format_standings(match["group"], table)
                except Exception:  # noqa: BLE001 - never let standings break the kick-off post
                    logger.exception("World Cup: kickoff standings failed; posting plain kick-off")
            draft = PostDraft(
                org_id=self.org_id,
                content=content,
                type="worldcup",
                channels=[c for c in platform_channels() if c in self.allowed],
                discord_channel=self.channel,
                source_type="worldcup",
                dedup_key=dedup_key,
                metadata={"event_type": ev.get("type")},
            )
            try:
                enqueue_post(self._conn, self._conn, draft, allowed_channels=self.allowed)
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
