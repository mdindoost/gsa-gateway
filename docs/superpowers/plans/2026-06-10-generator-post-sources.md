# Generator Post-Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give future admins a standard, validated way to turn their own content generators (any trigger they like) into delivered posts — produce a `PostDraft`, call `enqueue_post()`, and the existing scheduler/connectors deliver it — then migrate the World Cup tracker onto this system so it becomes the canonical live example.

**Architecture:** A new module `v2/core/publishing/sources.py` adds two things: `PostDraft` (the data an admin may set) + `enqueue_post()` (the single checked door that validates, dedups, and writes ONE `posts` row with `status='scheduled'`), and an optional `PostSource`/`SourceRunner` pair for admins who want a managed poll loop with failure isolation and a flood cap. The existing `SchedulerRunner` (ticking every 30s) → `PostPublisher.publish_due()` → `ConnectorRegistry` then delivers each row **with zero changes to publisher, registry, connectors, or schema**. World Cup is refactored from direct `registry.publish()` to `enqueue_post()`, going through the same buffered lane as everything else.

**Tech Stack:** Python 3.11, sqlite3 (STRICT tables, JSON1, WAL), asyncio, pytest. Live runtime: `.venv/bin/python -m bot.main` driven by `scripts/restart.sh`.

---

## Background facts (verified against the live code/DB on 2026-06-10)

- `posts` table (`v2/core/database/schema.py:81`) columns the draft may set: `org_id, type, title, content, channels (JSON), discord_channel, scheduled_for, source_type, source_id, metadata, created_by`. The publisher owns `status, sent_at, created_at`. `status` CHECK set: `scheduled|sending|sent|failed|cancelled`.
- `PostPublisher.publish_due()` (`publisher.py:104`) selects `status='scheduled' AND (scheduled_for IS NULL OR scheduled_for <= now)`. Any `status='scheduled'` row we insert is delivered automatically — it does not care who wrote it.
- `SchedulerRunner` (`scheduler_runner.py`) runs `Scheduler.tick()` every 30s and is **live** (`V2_SCHEDULER_ENABLED=true`; logs show `published:1, sent:1`). This is what delivers our rows.
- The GSA org is **`id=2`, slug `gsa`** (verified in the live DB). World Cup posts belong to it.
- World Cup today: `WorldCupRunner._loop` (`worldcup_runner.py:39`) builds an in-memory `Post` and calls `registry.publish(post)` directly (no posts row). It is wired in `bot/main.py:186-193` as `WorldCupRunner(registry, key, chan, interval)`, gated by `V2_WORLDCUP_ENABLED=true`.
- v2 test fixture pattern (`v2/tests/test_publisher.py`): `conn = create_all(":memory:")`, then `INSERT INTO organizations(name,slug,type) VALUES('GSA','gsa','gsa')`. No `conftest.py` exists in `v2/tests/`.
- `get_connection(db_path)` (`schema.py:325`) sets `PRAGMA foreign_keys=ON`, `busy_timeout=5000`, loads sqlite-vec — use it for any real-DB connection.

---

## File Structure

| File | Change | Responsibility after change |
|------|--------|------------------------------|
| `v2/core/publishing/sources.py` | **Create** | `PostDraft`, `EnqueueError`, `enqueue_post()`, `PostSource`, `SourceRunner` — the generator contract |
| `v2/tests/test_sources.py` | **Create** | Unit tests for validation, dedup, insert, and the runner's isolation/flood cap |
| `v2/integration/worldcup_runner.py` | **Modify** | Poll → build `PostDraft` → `enqueue_post()` instead of `registry.publish()`; owns its own connection + resolves org by slug |
| `v2/tests/test_worldcup.py` | **Modify** | Update the WC-runner behaviour test to assert a `posts` row is written (not a direct publish) |
| `bot/main.py` | **Modify** (`~186-193`) | Pass `db_path` + `org_slug` into `WorldCupRunner` |
| `README.md` | **Modify** | Update the World Cup architecture line to describe the buffered lane |
| `docs/POST_WORLDCUP_CLEANUP.md` | **Modify** (see Task 5 — NOT a blind delete) | Remove the now-false "WC tracker is v1" framing; keep the load-bearing rollback notes |

**Untouched (must keep working):** `publisher.py`, `registry.py`, `base.py`, `scheduler.py`, `schema.py`, all connectors, the `#ask-gsa` RAG chat, the Telegram bot.

---

## Pre-flight (do once, before Task 1)

- [ ] **Branch off main + back up the live DB.** We are on `main`. Per the project's gated-workflow rule, never build on `main` directly and always snapshot the DB first.

```bash
cd /home/md724/gsa-gateway
git checkout -b feat/generator-post-sources
mkdir -p .backups
cp gsa_gateway.db .backups/gsa_gateway.db.pre-sources-$(date +%Y%m%d-%H%M%S)
git rev-parse --abbrev-ref HEAD   # expect: feat/generator-post-sources
```

- [ ] **Capture the test baseline.** Run the two suites SEPARATELY — there is a
  known, pre-existing, order-dependent pollution bug (`bot/tests/test_worldcup.py`
  uses the deprecated `asyncio.get_event_loop()`, which raises `RuntimeError` if a
  `v2/` async test ran first in the same process). Running them separately avoids it.

```bash
.venv/bin/python -m pytest v2/tests/ -q  2>&1 | tail -3   # expect 55 passed
.venv/bin/python -m pytest bot/tests/ -q 2>&1 | tail -3   # expect 211 passed
```
Expected: 55 + 211 = 266 green. (Do NOT use `pytest v2/tests/ bot/tests/` in one
process — that order triggers the pre-existing event-loop pollution.)

---

## Task 1: `PostDraft` + `enqueue_post` — the validated door

**Files:**
- Create: `v2/core/publishing/sources.py`
- Test: `v2/tests/test_sources.py`

- [ ] **Step 1: Write the failing happy-path test**

Create `v2/tests/test_sources.py`:

```python
"""Tests for the generator post-sources contract (PostDraft + enqueue_post)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.database.schema import create_all
from v2.core.publishing.sources import PostDraft, EnqueueError, enqueue_post


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(2,'GSA','gsa','gsa')")
    c.commit()
    return c


def test_enqueue_inserts_scheduled_row(conn):
    draft = PostDraft(org_id=2, content="Hello world", type="broadcast",
                      channels=["discord"], source_type="test")
    pid = enqueue_post(conn, draft)
    row = conn.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()
    assert row["status"] == "scheduled"
    assert row["content"] == "Hello world"
    assert row["org_id"] == 2
    assert row["source_type"] == "test"
```

- [ ] **Step 2: Run it — verify it fails on the missing module**

Run: `.venv/bin/python -m pytest v2/tests/test_sources.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'v2.core.publishing.sources'`.

- [ ] **Step 3: Create `sources.py` with `PostDraft`, `EnqueueError`, and a minimal `enqueue_post`**

Create `v2/core/publishing/sources.py`:

```python
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
```

- [ ] **Step 4: Run the happy-path test — verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/test_sources.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add v2/core/publishing/sources.py v2/tests/test_sources.py
git commit -m "feat(v2): PostDraft + enqueue_post — generator post contract (insert path)"
```

- [ ] **Step 6: Write failing validation + dedup tests**

Append to `v2/tests/test_sources.py`:

```python
def test_rejects_unknown_org(conn):
    with pytest.raises(EnqueueError, match="does not exist"):
        enqueue_post(conn, PostDraft(org_id=999, content="x", type="broadcast"))


def test_rejects_inactive_org(conn):
    conn.execute("INSERT INTO organizations(id,name,slug,type,is_active) "
                 "VALUES(3,'Dead','dead','club',0)")
    with pytest.raises(EnqueueError, match="not active"):
        enqueue_post(conn, PostDraft(org_id=3, content="x", type="broadcast"))


def test_rejects_empty_content(conn):
    with pytest.raises(EnqueueError, match="content is empty"):
        enqueue_post(conn, PostDraft(org_id=2, content="   ", type="broadcast"))


def test_rejects_oversized_content(conn):
    with pytest.raises(EnqueueError, match="exceeds"):
        enqueue_post(conn, PostDraft(org_id=2, content="a" * 5000, type="broadcast"))


def test_rejects_unknown_type(conn):
    with pytest.raises(EnqueueError, match="not in allowed"):
        enqueue_post(conn, PostDraft(org_id=2, content="x", type="haxx"))


def test_rejects_unknown_channel(conn):
    with pytest.raises(EnqueueError, match="unknown channels"):
        enqueue_post(conn, PostDraft(org_id=2, content="x", type="broadcast",
                                     channels=["myspace"]))


def test_rejects_bad_scheduled_for(conn):
    with pytest.raises(EnqueueError, match="scheduled_for"):
        enqueue_post(conn, PostDraft(org_id=2, content="x", type="broadcast",
                                     scheduled_for="next tuesday"))


def test_rejects_unserializable_metadata(conn):
    with pytest.raises(EnqueueError, match="JSON"):
        enqueue_post(conn, PostDraft(org_id=2, content="x", type="broadcast",
                                     metadata={"bad": {1, 2, 3}}))


def test_dedup_returns_existing_id(conn):
    d = PostDraft(org_id=2, content="same content", type="broadcast", source_type="dup")
    first = enqueue_post(conn, d)
    second = enqueue_post(conn, d)
    assert first == second
    n = conn.execute("SELECT COUNT(*) FROM posts WHERE source_type='dup'").fetchone()[0]
    assert n == 1
```

- [ ] **Step 7: Run them — verify they fail (no validation/dedup yet)**

Run: `.venv/bin/python -m pytest v2/tests/test_sources.py -q`
Expected: the new tests FAIL (rows insert instead of raising; dedup inserts twice).

- [ ] **Step 8: Add validation + dedup to `enqueue_post`**

In `v2/core/publishing/sources.py`, replace the entire body of `enqueue_post` — from the line `valid_channels = DEFAULT_CHANNELS if allowed_channels is None else set(allowed_channels)` through the final `return cur.lastrowid` — with the full validated version below. (The replacement block opens with that same `valid_channels = …` line, so there is exactly one of it afterwards.)

```python
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
```

- [ ] **Step 9: Run the whole file — verify all pass**

Run: `.venv/bin/python -m pytest v2/tests/test_sources.py -q`
Expected: all PASS (10 passed).

- [ ] **Step 10: Commit**

```bash
git add v2/core/publishing/sources.py v2/tests/test_sources.py
git commit -m "feat(v2): enqueue_post validation + dedup guards"
```

---

## Task 2: `SourceRunner` — managed poll loop with isolation + flood cap

**Files:**
- Modify: `v2/core/publishing/sources.py`
- Test: `v2/tests/test_sources.py`

- [ ] **Step 1: Write failing tests for the runner**

Append to `v2/tests/test_sources.py`:

```python
import asyncio
from v2.core.publishing.sources import SourceRunner, MAX_PER_TICK


class _FakeSource:
    name = "fake"
    def __init__(self, drafts):
        self._drafts = drafts
        self.calls = 0
    async def poll(self):
        self.calls += 1
        if isinstance(self._drafts, Exception):
            raise self._drafts
        return self._drafts


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_runner_enqueues_polled_drafts(conn):
    src = _FakeSource([PostDraft(org_id=2, content="from source", type="broadcast",
                                 source_type="fake")])
    runner = SourceRunner(conn, src)
    n = _run(runner.run_once())
    assert n == 1
    assert conn.execute("SELECT COUNT(*) FROM posts WHERE source_type='fake'").fetchone()[0] == 1


def test_runner_isolates_a_throwing_source(conn):
    runner = SourceRunner(conn, _FakeSource(RuntimeError("boom")))
    # must not raise — a bad source cannot kill the tick
    n = _run(runner.run_once())
    assert n == 0


def test_runner_skips_invalid_drafts_but_keeps_good_ones(conn):
    drafts = [
        PostDraft(org_id=2, content="ok", type="broadcast", source_type="fake"),
        PostDraft(org_id=2, content="", type="broadcast", source_type="fake"),  # invalid
    ]
    runner = SourceRunner(conn, _FakeSource(drafts))
    n = _run(runner.run_once())
    assert n == 1


def test_runner_enforces_flood_cap(conn):
    many = [PostDraft(org_id=2, content=f"msg {i}", type="broadcast", source_type="flood")
            for i in range(MAX_PER_TICK + 10)]
    runner = SourceRunner(conn, _FakeSource(many))
    n = _run(runner.run_once())
    assert n == MAX_PER_TICK
```

- [ ] **Step 2: Run — verify failure**

Run: `.venv/bin/python -m pytest v2/tests/test_sources.py -k runner -q`
Expected: FAIL — `cannot import name 'SourceRunner'`.

- [ ] **Step 3: Implement `SourceRunner` + `MAX_PER_TICK`**

In `v2/core/publishing/sources.py`, add near the other constants:

```python
MAX_PER_TICK = 20   # flood cap: most rows one source may enqueue per tick
```

And append at the end of the module:

```python
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
            await self.run_once()
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
```

- [ ] **Step 4: Run the runner tests — verify pass**

Run: `.venv/bin/python -m pytest v2/tests/test_sources.py -q`
Expected: all PASS (14 passed).

- [ ] **Step 5: Commit**

```bash
git add v2/core/publishing/sources.py v2/tests/test_sources.py
git commit -m "feat(v2): SourceRunner — managed poll loop with isolation + flood cap"
```

---

## Task 3: Migrate World Cup onto the system

**Files:**
- Modify: `v2/integration/worldcup_runner.py`
- Test: `v2/tests/test_worldcup.py`

- [ ] **Step 1: Append a new behaviour test that expects a posts row**

> NOTE (verified by senior review): `v2/tests/test_worldcup.py` has **no** existing
> `WorldCupRunner`/`registry.publish` test — it only has tracker-detection and
> `format_event` tests, which are unaffected by this refactor. So **append** a new
> test; do not hunt for one to replace.

Add at the top of `v2/tests/test_worldcup.py` (if not already present):
`from v2.core.database.schema import create_all`. Then append:

```python
def test_worldcup_runner_enqueues_a_post(monkeypatch, tmp_path):
    import asyncio
    # isolate the tracker's on-disk state file (matches the existing tracker tests)
    monkeypatch.setattr("v2.integration.worldcup_tracker.STATE_FILE", tmp_path / "wc.json")
    from v2.integration.worldcup_runner import WorldCupRunner

    conn = create_all(":memory:")
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(2,'GSA','gsa','gsa')")
    conn.commit()

    runner = WorldCupRunner(registry=None, api_key="k", channel="world-cup-2026",
                            db_path=":memory:", org_slug="gsa")
    runner._conn = conn            # inject the test connection (start() would open its own)
    runner.org_id = 2
    runner.allowed = {"discord", "telegram"}

    async def fake_check():
        return [{"type": "goal", "match": {"id": 42}, "minute": 23,
                 "scoring_team": {"name": "Brazil"}}]
    runner.tracker.check_matches = fake_check
    # format_event is imported into the runner module's namespace; patch it there
    monkeypatch.setattr("v2.integration.worldcup_runner.format_event",
                        lambda ev: "GOOOOOAL Brazil 1-0")

    asyncio.new_event_loop().run_until_complete(runner._loop_once())

    row = conn.execute("SELECT * FROM posts WHERE type='worldcup'").fetchone()
    assert row is not None
    assert "GOOOOOAL" in row["content"]
    assert row["status"] == "scheduled"
    assert row["discord_channel"] == "world-cup-2026"
```

- [ ] **Step 2: Run — verify failure**

Run: `.venv/bin/python -m pytest v2/tests/test_worldcup.py -k enqueues -q`
Expected: FAIL — `WorldCupRunner.__init__` has no `db_path`/`org_slug`, no `_loop_once`.

- [ ] **Step 3: Refactor `worldcup_runner.py` to use `enqueue_post`**

Replace the full contents of `v2/integration/worldcup_runner.py` with:

```python
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
            # content coincidence. Confirm these field names against the event
            # dicts in worldcup_tracker.py when implementing.
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
```

- [ ] **Step 4: Run the WC tests — verify pass**

Run: `.venv/bin/python -m pytest v2/tests/test_worldcup.py -q`
Expected: PASS — the new test plus all existing tracker/`format_event` tests (which are untouched by this refactor).

- [ ] **Step 5: Commit**

```bash
git add v2/integration/worldcup_runner.py v2/tests/test_worldcup.py
git commit -m "refactor(v2): World Cup posts via enqueue_post — first buffered-lane source"
```

---

## Task 4: Wire the new WorldCupRunner signature into main.py

**Files:**
- Modify: `bot/main.py` (around lines 186-193)

- [ ] **Step 1: Update the construction call**

In `bot/main.py`, find:

```python
                    self.v2_worldcup_runner = WorldCupRunner(registry, key, chan, interval)
```

Replace with:

```python
                    org_slug = os.getenv("FOOTBALL_ORG_SLUG", "gsa")
                    self.v2_worldcup_runner = WorldCupRunner(
                        registry, key, chan, "gsa_gateway.db", org_slug, interval)
```

- [ ] **Step 2: Static check — the module imports and the bot compiles**

Run: `.venv/bin/python -c "import ast; ast.parse(open('bot/main.py').read()); print('main.py parses')"`
Run: `.venv/bin/python -c "import v2.integration.worldcup_runner as m; print('import ok')"`
Expected: both print success.

- [ ] **Step 3: Commit**

```bash
git add bot/main.py
git commit -m "feat: pass db_path + org_slug into WorldCupRunner (buffered lane)"
```

---

## Task 5: Documentation cleanup

**Files:**
- Modify: `README.md`
- Modify (NOT blind-delete): `docs/POST_WORLDCUP_CLEANUP.md`

> **Why not just delete `POST_WORLDCUP_CLEANUP.md`:** it is mixed. Its central claim — "the World Cup tracker is **v1** code" — is now false (the tracker is `v2/integration/worldcup_runner.py` and, after this work, goes through the buffered lane). But it ALSO holds load-bearing rollback notes (don't delete `chroma_db/`, `gsa_gateway.db.backup_*`, `run_telegram.py`) and Tier-2/3 cleanup TODOs that are NOT about World Cup. Deleting the file would lose those. So: strip the outdated WC framing, keep the rollback/cleanup notes.

- [ ] **Step 1: Fix the README World Cup architecture line**

In `README.md` find (line ~67):

```
  chat, intent routing, reminders, daily digest, and the World Cup tracker.
```

This stays accurate. Then find the architecture bullet describing how content is sent and ensure the World Cup is described as going through the posts pipeline. If the README claims World Cup is sent "directly," update it to: "The World Cup tracker is the reference *content generator*: it polls live match data and enqueues posts through the standard `enqueue_post` contract, which the scheduler then delivers." (Skip if no such "directly" claim exists — `grep -n directly README.md`.)

- [ ] **Step 2: Update `docs/POST_WORLDCUP_CLEANUP.md`**

Remove the opening framing that calls the World Cup tracker "v1 code" and the WC-specific deferral language. Keep the "Do NOT delete these" rollback section and the Tier-2/Tier-3 TODOs. Add a one-line note at the top:

```markdown
> **Update 2026-06-10:** The World Cup tracker is v2 (`v2/integration/worldcup_runner.py`)
> and now publishes through the standard generator contract (`enqueue_post`). The
> tournament-specific deferral below is obsolete; the rollback/cleanup notes that
> follow are retained because they are not World-Cup-specific.
```

- [ ] **Step 3: Commit**

```bash
git add README.md docs/POST_WORLDCUP_CLEANUP.md
git commit -m "docs: World Cup now uses the generator contract; retire stale cleanup framing"
```

---

## Task 6: Full verification + gated live rollout

**Files:** none modified — verification + controlled restart.

- [ ] **Step 1: Run the entire suite (two suites SEPARATELY — see pre-flight note)**

```bash
.venv/bin/python -m pytest v2/tests/ -q  2>&1 | tail -5   # v2 incl. new test_sources.py + WC test
.venv/bin/python -m pytest bot/tests/ -q 2>&1 | tail -3   # expect 211 (unchanged)
```
Expected: `v2/tests/` green at baseline 55 + new `test_sources.py` (≈14) + the new WC runner test (≈70); `bot/tests/` still 211. Do NOT combine the two paths in one `pytest` invocation (pre-existing event-loop pollution).

- [ ] **Step 2: Dry-run enqueue against a COPY of the live DB (no live writes)**

```bash
cp gsa_gateway.db /tmp/sources-dryrun.db
.venv/bin/python -c "
from v2.core.database.schema import get_connection
from v2.core.publishing.sources import PostDraft, enqueue_post
c = get_connection('/tmp/sources-dryrun.db')
pid = enqueue_post(c, PostDraft(org_id=2, content='dry-run test post', type='broadcast',
                                channels=['discord'], source_type='dryrun'))
row = c.execute('SELECT status,type,content FROM posts WHERE id=?', (pid,)).fetchone()
print('enqueued id', pid, '->', dict(row))
# dedup check
pid2 = enqueue_post(c, PostDraft(org_id=2, content='dry-run test post', type='broadcast',
                                 channels=['discord'], source_type='dryrun'))
print('dedup returns same id:', pid == pid2)
"
rm -f /tmp/sources-dryrun.db
```
Expected: prints a scheduled row and `dedup returns same id: True`. **The live DB is untouched.**

- [ ] **Step 3: GATED — confirm with the user before restarting the live bot.**

Restarting applies the new WorldCupRunner signature live. World Cup is currently `V2_WORLDCUP_ENABLED=true` / `FOOTBALL_ENABLED=false`. Surface to the user: ready to `bash scripts/restart.sh`? Do not proceed without explicit approval.

- [ ] **Step 4: Restart and verify the boot is clean**

```bash
bash scripts/restart.sh
sleep 6
grep -E "V2 World Cup tracker started|V2 scheduler|Error|Traceback" gsa_gateway.log | tail -8
```
Expected: `V2 World Cup tracker started (... org=2 ...)`, scheduler alive, no tracebacks. One Discord + one Telegram process (restart.sh guarantees it).

- [ ] **Step 5: Confirm end-to-end on the live DB (read-only check)**

After a real match event (or by leaving it running), confirm a `worldcup` post appears and is delivered:

```bash
.venv/bin/python -c "
import sqlite3; c=sqlite3.connect('gsa_gateway.db'); c.row_factory=sqlite3.Row
for r in c.execute(\"SELECT id,status,sent_at,substr(content,1,40) c FROM posts WHERE type='worldcup' ORDER BY id DESC LIMIT 5\"):
    print(dict(r))
"
```
Expected (once an event fires): rows transitioning `scheduled → sent` within ~30s (the scheduler interval).

- [ ] **Step 6: Final commit / branch is ready for review**

```bash
git log --oneline -8
.venv/bin/python -m pytest v2/tests/ bot/tests/ -q 2>&1 | tail -3
```
Then hand off: request the senior-engineer review of the implementation before merging to `main` (per the project's standing rule).

---

## Self-Review (completed by plan author)

**1. Spec coverage:**
- "Build the system" → Tasks 1 (PostDraft + enqueue_post) & 2 (PostSource/SourceRunner). ✓
- "Then change World Cup to use the system" → Task 3 (runner refactor) + Task 4 (wiring). ✓
- "World Cup be that example" → Task 3 makes WC the first/only buffered-lane source; docstring + README (Task 5) call it the reference. ✓
- "Remove old docs regarding World Cup" → Task 5 — handled as an update, not a blind delete, because the file holds non-WC rollback notes (flagged for the user). ✓
- Senior-review guards (validation, dedup, flood cap, failure isolation) → Task 1 Step 8 + Task 2. ✓

**2. Placeholder scan:** No TBD/TODO-as-implementation. Every code step shows full code; every run step shows the command + expected result. The one judgment call (README "directly" line) is conditional with an explicit `grep` to decide. ✓

**3. Type consistency:** `PostDraft`, `EnqueueError`, `enqueue_post(conn, draft, *, allowed_channels=None)`, `PostSource.poll()`, `SourceRunner(conn, source, *, interval, allowed_channels)` with `run_once()/start()/stop()`, `MAX_PER_TICK`, `WorldCupRunner(registry, api_key, channel, db_path, org_slug, interval)` with `_loop_once()` — names are identical across Tasks 1–4 and the tests. ✓

**Risks (reviewed):**
- World Cup Telegram routing — **RESOLVED/verified.** Org 2 (gsa) has `org.telegram_channel = @GSAGateWayNJIT` and `default.channel.worldcup = world-cup-2026` in `settings` (confirmed in the live DB 2026-06-10), so both platforms route correctly. Task 6 Step 5 still confirms a live `sent`.
- `MAX_CONTENT = 4000` vs Discord's 2000-char hard limit — a 2000–4000 char post passes validation but Discord would reject the send (Telegram up to 4096 is fine); the post is still marked `sent` if any platform succeeds. WC events are far under 2000, so no live impact. Acceptable for v1; documented, not blocking.

**Senior pre-implementation review:** completed 2026-06-10. All 3 blocking findings folded into this plan (Task 3 Step 1 reframed as append; Step 8 anchor made explicit; `import asyncio` added to the module header). Nice-to-haves applied: isolated tracker `STATE_FILE` in the WC test, explicit WC `dedup_key`, `SourceRunner` connection-ownership documented.
