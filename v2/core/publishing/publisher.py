"""PostPublisher — turn ``posts`` rows into deliveries via the ConnectorRegistry.

The publisher is the only thing that reads the ``posts`` table for sending. It
builds a connector-layer ``Post`` (resolving platforms, per-platform channels and
the rendered signature from settings), drives the status lifecycle
(scheduled → sending → sent/failed), stamps ``sent_at``, and lets the registry
record per-platform results in ``post_deliveries``.

It knows about platforms only by *name* (to map channels); it never imports a
connector class. Adding a platform changes nothing here.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from v2.core.connectors.base import Post
from v2.core.database.queries import get_setting, get_setting_typed

logger = logging.getLogger(__name__)

# post.type -> the settings channel key (default.channel.<key>)
_TYPE_CHANNEL_KEY = {
    "one_time": "broadcast",
    "broadcast": "broadcast",
    "digest": "broadcast",
    "recurring_instance": "broadcast",
    "event_announcement": "event",
    "event_reminder": "event",
    "mathcafe": "mathcafe",
    "worldcup": "worldcup",
}


def _now_str() -> str:
    # UTC, matching SQLite datetime('now') and the UTC-canonical scheduled_for.
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class PostPublisher:
    def __init__(self, conn, registry, signatures):
        self.conn = conn
        self.registry = registry
        self.signatures = signatures

    # ── building the outgoing Post ───────────────────────────────────────────
    def _platforms(self, row) -> list[str]:
        declared = json.loads(row["channels"] or "[]")
        if declared:
            return declared
        return get_setting_typed(self.conn, row["org_id"], "default.platforms", ["discord"]) \
            or ["discord"]

    def _discord_channel(self, row) -> str | None:
        if row["discord_channel"]:
            return row["discord_channel"]
        key = _TYPE_CHANNEL_KEY.get(row["type"], "broadcast")
        return (get_setting(self.conn, row["org_id"], f"default.channel.{key}")
                or get_setting(self.conn, row["org_id"], "default.channel.broadcast"))

    def _telegram_channel(self, row) -> str | None:
        return get_setting(self.conn, row["org_id"], "org.telegram_channel")

    def _groupme_group(self, row) -> str | None:
        return get_setting(self.conn, row["org_id"], "org.groupme_group")

    def build_post(self, row) -> Post:
        platforms = self._platforms(row)
        signature = self.signatures.render(row["org_id"], row["signature"])
        platform_channels: dict[str, str] = {}
        if "discord" in platforms:
            platform_channels["discord"] = self._discord_channel(row)
        if "telegram" in platforms:
            platform_channels["telegram"] = self._telegram_channel(row)
        if "groupme" in platforms:
            platform_channels["groupme"] = self._groupme_group(row) or "GSAGateWayNJIT"
        return Post(
            id=row["id"], content=row["content"], channels=platforms,
            signature=signature or None, platform_channels=platform_channels,
            metadata=json.loads(row["metadata"] or "{}"),
        )

    # ── publishing ───────────────────────────────────────────────────────────
    async def publish_post(self, post_id: int):
        row = self.conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        if row is None:
            logger.warning("publish_post: no post id=%s", post_id)
            return []
        if row["status"] in ("sent", "cancelled"):
            logger.debug("publish_post: post id=%s already %s, skipping", post_id, row["status"])
            return []

        self.conn.execute("UPDATE posts SET status='sending' WHERE id=?", (post_id,))
        self.conn.commit()

        results = await self.registry.publish(self.build_post(row))
        delivered = any(r.success for r in results)
        status = "sent" if delivered else "failed"
        self.conn.execute(
            "UPDATE posts SET status=?, sent_at=? WHERE id=?",
            (status, _now_str(), post_id),
        )
        self.conn.commit()
        logger.debug("publish_post id=%s -> %s (%d deliveries)", post_id, status, len(results))
        return results

    async def publish_due(self, now: str | None = None) -> dict:
        now = now or _now_str()
        due = self.conn.execute(
            "SELECT id FROM posts WHERE status='scheduled' "
            "AND (scheduled_for IS NULL OR scheduled_for <= ?) "
            "ORDER BY scheduled_for IS NULL DESC, scheduled_for",
            (now,),
        ).fetchall()
        summary = {"published": 0, "sent": 0, "failed": 0}
        for r in due:
            results = await self.publish_post(r["id"])
            summary["published"] += 1
            summary["sent" if any(x.success for x in results) else "failed"] += 1
        return summary
