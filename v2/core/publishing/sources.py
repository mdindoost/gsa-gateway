"""Generator post-sources contract — the standard, validated door for turning
admin-written content generators into delivered posts.

A generator (ANY trigger the admin likes — poll loop, cron, dashboard button)
produces ``PostDraft`` objects and calls ``enqueue_post()``. enqueue_post
validates the draft, dedups it, and writes ONE ``posts`` row (status='scheduled').
The existing SchedulerRunner -> PostPublisher.publish_due() -> ConnectorRegistry
then delivers it. Nothing in publisher/registry/connectors/schema changes.

Admins never set status/sent_at/created_at and never hold the db connection
directly (a SourceRunner owns it). Validation here is the SINGLE checked door:
arbitrary admin code cannot push malformed / oversized / unsafe content
downstream into Discord/Telegram.
"""
from __future__ import annotations

import asyncio  # used by SourceRunner (Task 2)
import hashlib
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

# Discord caps a message at 2000 chars, Telegram at 4096; the connector appends
# the signature AFTER this, so leave headroom.
MAX_CONTENT = 4000
MAX_TITLE = 256
MAX_META_BYTES = 4096
ALLOWED_TYPES = {
    "one_time", "recurring_instance", "event_announcement", "event_reminder",
    "mathcafe", "worldcup", "broadcast", "digest", "generator",
}
DEFAULT_CHANNELS = {"discord", "telegram"}
MAX_PER_TICK = 20   # flood cap: most rows one source may enqueue per tick


@dataclass
class PostDraft:
    """Everything a generator is allowed to set on a post. Maps to ``posts``
    columns; status/sent_at/created_at are owned by the publisher, never here."""
    org_id: int
    content: str
    type: str = "generator"
    title: str | None = None
    channels: list[str] = field(default_factory=list)     # registered connector names
    discord_channel: str | None = None
    scheduled_for: str | None = None                      # "YYYY-MM-DD HH:MM:SS" UTC, None = asap
    source_type: str = "generator"
    source_id: int | None = None                          # natural dedup key when integer
    dedup_key: str | None = None                          # fallback dedup key when no source_id
    metadata: dict = field(default_factory=dict)
    created_by: str | None = None


class EnqueueError(ValueError):
    """Raised when a draft fails validation. Never reaches the connectors."""


def _dedup_key(draft: "PostDraft") -> str:
    if draft.source_id is not None:
        return f"{draft.source_type}:{draft.source_id}"
    if draft.dedup_key:
        return f"{draft.source_type}:{draft.dedup_key}"
    digest = hashlib.sha1(
        f"{draft.org_id}|{draft.type}|{draft.content}".encode()
    ).hexdigest()
    return f"{draft.source_type}:auto:{digest}"


def enqueue_post(conn, draft: "PostDraft", *, allowed_channels=None) -> int:
    """Validate, dedup, and insert ONE posts row (status='scheduled').

    Returns the new post id, or the existing id when the draft is a duplicate.
    Raises EnqueueError on invalid input. ``allowed_channels`` (a set of
    registered connector names) restricts the channels a draft may target; when
    None, defaults to {"discord","telegram"}.
    """
    valid_channels = DEFAULT_CHANNELS if allowed_channels is None else set(allowed_channels)

    # 1) validate
    if not isinstance(draft.org_id, int):
        raise EnqueueError("org_id must be an int")
    org = conn.execute(
        "SELECT is_active FROM organizations WHERE id=?", (draft.org_id,)
    ).fetchone()
    if org is None:
        raise EnqueueError(f"org_id {draft.org_id} does not exist")
    if not org["is_active"]:
        raise EnqueueError(f"org_id {draft.org_id} is not active")

    content = (draft.content or "").strip()
    if not content:
        raise EnqueueError("content is empty")
    if len(content) > MAX_CONTENT:
        raise EnqueueError(f"content exceeds {MAX_CONTENT} chars ({len(content)})")
    if draft.title and len(draft.title) > MAX_TITLE:
        raise EnqueueError(f"title exceeds {MAX_TITLE} chars")
    if draft.type not in ALLOWED_TYPES:
        raise EnqueueError(f"type '{draft.type}' not in allowed set {sorted(ALLOWED_TYPES)}")
    bad = [c for c in (draft.channels or []) if c not in valid_channels]
    if bad:
        raise EnqueueError(f"unknown channels: {bad}")
    if draft.scheduled_for is not None:
        try:
            datetime.strptime(draft.scheduled_for, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            raise EnqueueError("scheduled_for must be 'YYYY-MM-DD HH:MM:SS' UTC or None")
    try:
        json.dumps(draft.metadata or {})
    except (TypeError, ValueError) as exc:
        raise EnqueueError(f"metadata not JSON-serializable: {exc}")

    # 2) dedup (by stable key stored in metadata._dedup_key, scoped to org+source_type)
    key = _dedup_key(draft)
    existing = conn.execute(
        "SELECT id FROM posts WHERE org_id=? AND source_type=? "
        "AND json_extract(metadata, '$._dedup_key')=?",
        (draft.org_id, draft.source_type, key),
    ).fetchone()
    if existing is not None:
        logger.debug("enqueue_post: dedup hit key=%s -> id=%s", key, existing["id"])
        return existing["id"]

    # 3) metadata size cap (after we know we're inserting)
    meta = dict(draft.metadata or {})
    meta["_dedup_key"] = key
    meta_json = json.dumps(meta)
    if len(meta_json.encode()) > MAX_META_BYTES:
        raise EnqueueError(f"metadata exceeds {MAX_META_BYTES} bytes")

    # 4) insert
    cur = conn.execute(
        "INSERT INTO posts(org_id, type, title, content, channels, discord_channel, "
        "scheduled_for, status, source_type, source_id, metadata, created_by) "
        "VALUES (?,?,?,?,?,?,?,'scheduled',?,?,?,?)",
        (draft.org_id, draft.type, draft.title, content,
         json.dumps(draft.channels or []), draft.discord_channel, draft.scheduled_for,
         draft.source_type, draft.source_id, meta_json, draft.created_by),
    )
    conn.commit()
    logger.info("enqueue_post: queued post id=%s type=%s org=%s key=%s",
                cur.lastrowid, draft.type, draft.org_id, key)
    return cur.lastrowid


class PostSource(ABC):
    """Optional structure for poll-style generators. Implement ``poll()`` to
    return drafts; a ``SourceRunner`` owns the loop, the connection, failure
    isolation, and the flood cap. (Filled in Task 2.)"""
    name: str = "source"

    @abstractmethod
    async def poll(self) -> list["PostDraft"]:
        ...


class SourceRunner:
    """Owns the loop + failure isolation for one PostSource. Per tick it polls
    the source, enqueues up to MAX_PER_TICK drafts, and swallows source errors
    and per-draft validation errors so one bad source never kills the loop or
    the bots.

    Connection ownership: the runner BORROWS a caller-owned ``conn`` (opened via
    ``get_connection`` on the loop thread) and never closes it — the caller owns
    its lifecycle. (WorldCupRunner, by contrast, opens and owns its own
    connection because it predates this and manages its own start/stop.)"""

    def __init__(self, conn, source: "PostSource", *, interval: int = 60,
                 allowed_channels=None):
        self.conn = conn
        self.source = source
        self.interval = interval
        self.allowed_channels = allowed_channels
        self._task = None
        self._running = False

    async def run_once(self) -> int:
        """One tick. Returns how many posts were enqueued."""
        try:
            drafts = await self.source.poll()
        except Exception:  # noqa: BLE001 - a bad source must not kill the tick
            logger.exception("source %s.poll() failed", getattr(self.source, "name", "?"))
            return 0
        if len(drafts) > MAX_PER_TICK:
            logger.warning("source %s produced %d drafts; capping at %d",
                           self.source.name, len(drafts), MAX_PER_TICK)
            drafts = drafts[:MAX_PER_TICK]
        enqueued = 0
        for d in drafts:
            try:
                enqueue_post(self.conn, d, allowed_channels=self.allowed_channels)
                enqueued += 1
            except EnqueueError as exc:
                logger.warning("source %s: dropped invalid draft: %s", self.source.name, exc)
        return enqueued

    async def _loop(self):
        while self._running:
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001 - a bad tick must not kill the loop
                logger.exception("SourceRunner tick for %s failed unexpectedly",
                                 getattr(self.source, "name", "?"))
            await asyncio.sleep(self.interval)

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
