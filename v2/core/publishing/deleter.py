"""PostDeleter — unsend due posts' delivered messages, marking the DB (records immortal).

Mirror of PostPublisher.publish_due: poll posts whose delete_at has passed, route each delivery
to its platform connector to UNSEND it, and record a per-delivery delete_status. It NEVER issues a
DELETE against posts/post_deliveries — only UPDATEs (the immortal-records hard line). A platform
"message not found" counts as success (the goal state — message absent — is achieved).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


def _is_deletable(mid: str | None) -> bool:
    """True if there is a message worth handing to a connector. Excludes an empty id and the
    pre-Phase-0 Telegram sentinel ('telegram-broadcast', which is not a real id). GroupMe's
    synthetic 'groupme:…' IS handed through — its connector returns delete_unsupported, which is
    the correct, honest per-delivery outcome (vs. 'not_applicable' = nothing was ever delivered)."""
    return bool(mid) and mid != "telegram-broadcast"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class PostDeleter:
    def __init__(self, conn, registry):
        self.conn = conn
        self.registry = registry

    async def delete_due(self, now: str | None = None) -> dict:
        now = now or _now()
        due = self.conn.execute(
            "SELECT id FROM posts WHERE delete_at IS NOT NULL AND delete_at <= ? "
            "AND status='sent' AND deleted_at IS NULL ORDER BY delete_at",
            (now,),
        ).fetchall()
        summary = {"posts": 0, "deleted": 0, "unsupported": 0, "failed": 0}
        for row in due:
            summary["posts"] += 1
            await self._delete_one_post(row["id"], summary)
        if summary["posts"]:
            logger.info("PostDeleter.delete_due: %s", summary)
        return summary

    async def _delete_one_post(self, post_id: int, summary: dict) -> None:
        deliveries = self.conn.execute(
            "SELECT id, platform, channel, message_id, status, delete_attempts "
            "FROM post_deliveries WHERE post_id=? AND delete_status IS NULL",
            (post_id,),
        ).fetchall()
        for d in deliveries:
            # nothing was delivered, or no real/deletable id -> nothing to unsend
            if d["status"] != "success" or not _is_deletable(d["message_id"]):
                self._mark(d["id"], "not_applicable", None, d["delete_attempts"])
                continue
            result = await self.registry.delete_delivery(d["platform"], d["channel"], d["message_id"])
            err = result.error or ""
            err_l = err.lower()
            # 404 = success (message already gone = goal achieved). The PRIMARY signal is
            # result.success — Discord's adapter catches discord.NotFound and returns success, so it
            # never relies on the string match. The "not found"/"unknown message" substrings are a
            # SECONDARY defensive fallback for connectors that surface a 404 only as an error string;
            # tighten/replace with a structured signal if more delete-capable connectors are added.
            if result.success or "not found" in err_l or "unknown message" in err_l:
                self._mark(d["id"], "deleted", None, d["delete_attempts"])
                summary["deleted"] += 1
            elif "unsupported" in err_l:
                self._mark(d["id"], "delete_unsupported", err, d["delete_attempts"])
                summary["unsupported"] += 1
            elif d["delete_attempts"] + 1 >= MAX_ATTEMPTS:
                self._mark(d["id"], "delete_failed", err, d["delete_attempts"] + 1)
                summary["failed"] += 1
            else:
                # transient: leave delete_status NULL to retry next tick, bump the counter
                self.conn.execute(
                    "UPDATE post_deliveries SET delete_attempts=?, delete_error=? WHERE id=?",
                    (d["delete_attempts"] + 1, err, d["id"]))
                self.conn.commit()
        # stamp the post rollup only when every delivery has a terminal delete_status
        remaining = self.conn.execute(
            "SELECT 1 FROM post_deliveries WHERE post_id=? AND delete_status IS NULL LIMIT 1",
            (post_id,)).fetchone()
        if remaining is None:
            self.conn.execute("UPDATE posts SET deleted_at=? WHERE id=?", (_now(), post_id))
            self.conn.commit()

    def _mark(self, delivery_id: int, status: str, error: str | None, attempts: int) -> None:
        self.conn.execute(
            "UPDATE post_deliveries SET delete_status=?, deleted_at=?, delete_error=?, "
            "delete_attempts=? WHERE id=?",
            (status, _now() if status == "deleted" else None, error, attempts, delivery_id))
        self.conn.commit()
