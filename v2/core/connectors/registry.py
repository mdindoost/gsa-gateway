"""ConnectorRegistry — fan a Post out to every targeted platform in parallel.

The publisher and scheduler talk only to this registry. ``publish`` never raises:
each delivery is wrapped, failures become failed ``DeliveryResult`` rows, and (if
a db connection is supplied and the post has an id) every result is recorded in
``post_deliveries`` for a permanent audit trail.
"""

from __future__ import annotations

import asyncio
import logging

from .base import BaseConnector, DeliveryResult, Post

logger = logging.getLogger(__name__)


class ConnectorRegistry:
    def __init__(self, conn=None):
        self._connectors: dict[str, BaseConnector] = {}
        self.conn = conn  # optional sqlite connection for post_deliveries logging

    # ── registration ────────────────────────────────────────────────────────
    def register(self, connector: BaseConnector) -> None:
        self._connectors[connector.name] = connector

    def get(self, name: str) -> BaseConnector | None:
        return self._connectors.get(name)

    def get_enabled(self) -> list[BaseConnector]:
        return [c for c in self._connectors.values() if c.enabled]

    # ── delivery ─────────────────────────────────────────────────────────────
    def _resolve_targets(self, post: Post, target_platforms: list[str] | None):
        if target_platforms is not None:
            wanted = target_platforms
        elif post.channels:
            wanted = post.channels
        else:
            wanted = [c.name for c in self.get_enabled()]
        wanted_set = set(wanted)
        return [c for c in self.get_enabled() if c.name in wanted_set]

    async def _deliver(self, connector: BaseConnector, post: Post) -> DeliveryResult:
        channel = post.channel_for(connector.name)
        try:
            content = connector.format_content(post.content, post.signature)
            if post.media_path:
                return await connector.send_media(content, post.media_path, channel, post.metadata)
            if post.buttons:
                return await connector.send_interactive(content, post.buttons, channel, post.metadata)
            return await connector.send_text(content, channel, post.metadata)
        except Exception as exc:  # noqa: BLE001 - a connector must never sink the batch
            logger.warning("connector %s raised during delivery: %s", connector.name, exc)
            return DeliveryResult(False, connector.name, channel=channel, error=str(exc))

    async def publish(self, post: Post,
                      target_platforms: list[str] | None = None) -> list[DeliveryResult]:
        targets = self._resolve_targets(post, target_platforms)
        if not targets:
            logger.debug("publish: no enabled targets for post id=%s", post.id)
            return []
        results = await asyncio.gather(*(self._deliver(c, post) for c in targets))
        self._log_deliveries(post, results)
        ok = sum(r.success for r in results)
        logger.debug("publish post id=%s → %d/%d delivered (%s)",
                     post.id, ok, len(results), ",".join(r.platform for r in results))
        return list(results)

    async def delete_delivery(self, platform: str, channel, message_id) -> DeliveryResult:
        """Unsend ONE delivered message via its platform connector. Never raises. Unknown or
        disabled platform → a failed result (the deleter records it, never crashes the sweep)."""
        connector = self._connectors.get(platform)
        if connector is None or not connector.enabled:
            return DeliveryResult(False, platform, channel=channel, message_id=message_id,
                                  error="no connector")
        try:
            return await connector.delete_message(channel, message_id)
        except Exception as exc:  # noqa: BLE001
            return DeliveryResult(False, platform, channel=channel, message_id=message_id,
                                  error=str(exc))

    async def health_check_all(self) -> dict[str, bool]:
        connectors = self.get_enabled()
        async def _safe(c):
            try:
                return await c.health_check()
            except Exception:  # noqa: BLE001
                return False
        states = await asyncio.gather(*(_safe(c) for c in connectors))
        return {c.name: s for c, s in zip(connectors, states)}

    # ── audit trail ──────────────────────────────────────────────────────────
    def _log_deliveries(self, post: Post, results: list[DeliveryResult]) -> None:
        if self.conn is None or post.id is None:
            return
        self.conn.executemany(
            "INSERT INTO post_deliveries(post_id,platform,channel,message_id,status,error,sent_at) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                (post.id, r.platform, r.channel, r.message_id, r.status, r.error,
                 r.sent_at.strftime("%Y-%m-%d %H:%M:%S"))
                for r in results
            ],
        )
        self.conn.commit()
