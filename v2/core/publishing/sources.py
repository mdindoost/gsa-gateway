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

import asyncio
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

    # 1) insert (validation added in Step 7)
    meta = dict(draft.metadata or {})
    key = _dedup_key(draft)
    meta["_dedup_key"] = key
    cur = conn.execute(
        "INSERT INTO posts(org_id, type, title, content, channels, discord_channel, "
        "scheduled_for, status, source_type, source_id, metadata, created_by) "
        "VALUES (?,?,?,?,?,?,?,'scheduled',?,?,?,?)",
        (draft.org_id, draft.type, draft.title, draft.content.strip(),
         json.dumps(draft.channels or []), draft.discord_channel, draft.scheduled_for,
         draft.source_type, draft.source_id, json.dumps(meta), draft.created_by),
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
