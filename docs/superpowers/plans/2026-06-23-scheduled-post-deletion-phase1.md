# Scheduled Post-Deletion — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add timer-based deletion of already-sent posts — Discord deletion working end-to-end, GroupMe best-effort `delete_unsupported`, the scheduler unsending due posts and marking the DB.

**Architecture:** `delete_at` on `posts` (the schedule) + per-delivery `delete_status` on `post_deliveries` (the outcome). A `PostDeleter.delete_due()` mirrors `PostPublisher.publish_due()` and runs after it in the `Scheduler.tick()` loop. Deletion routes through the `ConnectorRegistry` to each platform's `delete_message`. Records are immortal — the deleter only `UPDATE`s.

**Tech Stack:** Python 3.11+, sqlite3 (STRICT tables), discord.py, pytest. Spec: `docs/superpowers/specs/2026-06-23-scheduled-post-deletion-design.md`.

## Global Constraints
- **HARD LINE — post records immortal:** the deleter NEVER issues `DELETE` against `posts`/`post_deliveries`; only `UPDATE` (sets `deleted_at`/`delete_status`). "Delete" = platform unsend.
- **Idempotency:** a platform 404 / "message not found" counts as **success** (`delete_status='deleted'`).
- **Per-platform `channel`:** Discord stores the channel *name*, Telegram the resolved *chat_id*, GroupMe the setting value. The deleter passes `(channel, message_id)` to the platform's OWN connector and never interprets `channel` itself.
- **No new deps.** Migrations are additive `ALTER TABLE ADD COLUMN` in `schema._COLUMN_MIGRATIONS` (try/except idempotent).
- Phase 1 does NOT implement Telegram deletion (that's Phase 2) — Telegram deliveries get `delete_status='delete_unsupported'` until then, EXCEPT the default connector behavior already yields that, so no special-casing.
- Times are naive-UTC strings `"%Y-%m-%d %H:%M:%S"` (matches `scheduled_for`/`sent_at`).

---

### Task 1: Schema — add deletion columns

**Files:**
- Modify: `v2/core/database/schema.py` (the `_COLUMN_MIGRATIONS` list ~line 540; the `post_deliveries` CREATE-TABLE comment ~line 124)
- Test: `v2/tests/test_deletion_schema.py` (create)

**Interfaces:**
- Produces: columns `posts.delete_at`, `posts.deleted_at`, `post_deliveries.delete_status`, `post_deliveries.deleted_at`, `post_deliveries.delete_error`, `post_deliveries.delete_attempts`.

- [ ] **Step 1: Write the failing test**
```python
# v2/tests/test_deletion_schema.py
import sys, sqlite3
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path: sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all

def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}

def test_deletion_columns_exist():
    conn = create_all(":memory:")
    assert {"delete_at", "deleted_at"} <= _cols(conn, "posts")
    assert {"delete_status", "deleted_at", "delete_error", "delete_attempts"} <= _cols(conn, "post_deliveries")
    conn.close()
```

- [ ] **Step 2: Run it — expect FAIL** (`KeyError`/assert: columns missing)
Run: `python3 -m pytest v2/tests/test_deletion_schema.py -q`
Expected: FAIL (assert — columns not present).

- [ ] **Step 3: Add the migrations**
In `v2/core/database/schema.py`, append to `_COLUMN_MIGRATIONS` (after the judging rows):
```python
    # scheduled post-deletion (2026-06-23): platform unsend + per-delivery outcome.
    # delete_status values written by code: 'deleted'|'delete_unsupported'|'delete_failed'|'not_applicable'
    # (CHECK omitted — SQLite ALTER ADD COLUMN can't add it to an existing table; the deleter is the sole writer).
    ("posts",           "delete_at",       "TEXT"),
    ("posts",           "deleted_at",      "TEXT"),
    ("post_deliveries", "delete_status",   "TEXT"),
    ("post_deliveries", "deleted_at",      "TEXT"),
    ("post_deliveries", "delete_error",    "TEXT"),
    ("post_deliveries", "delete_attempts", "INTEGER NOT NULL DEFAULT 0"),
```
Also update the `post_deliveries` CREATE-TABLE comment on the `channel` line (~line 128) to:
```
    channel    TEXT,                          -- per-platform: discord=channel NAME, telegram=resolved chat_id, groupme=setting (used as-is by that platform's delete_message)
```

- [ ] **Step 4: Run it — expect PASS**
Run: `python3 -m pytest v2/tests/test_deletion_schema.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add v2/core/database/schema.py v2/tests/test_deletion_schema.py
git commit -m "feat(publishing): add delete_at/deleted_at + per-delivery deletion columns (deletion Phase 1)"
```

---

### Task 2: Connector interface — default `delete_message` (unsupported)

**Files:**
- Modify: `v2/core/connectors/base.py` (add method to `BaseConnector` ~after line 91)
- Test: `v2/tests/test_connector_delete.py` (create)

**Interfaces:**
- Produces: `BaseConnector.delete_message(self, channel: str | None, message_id: str) -> DeliveryResult` (non-abstract; default returns `success=False, error="delete unsupported"`). Later tasks call it through the registry.

- [ ] **Step 1: Write the failing test**
```python
# v2/tests/test_connector_delete.py
import sys, asyncio
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path: sys.path.insert(0, str(REPO))
from v2.core.connectors.groupme_connector import GroupMeConnector

def _run(c): return asyncio.get_event_loop().run_until_complete(c)

def test_default_delete_is_unsupported():
    conn = GroupMeConnector(client=None)          # GroupMe inherits the default (no override)
    r = _run(conn.delete_message("grp", "123"))
    assert r.success is False
    assert "unsupported" in (r.error or "").lower()
    assert r.platform == "groupme"
```

- [ ] **Step 2: Run it — expect FAIL** (`AttributeError: 'GroupMeConnector' object has no attribute 'delete_message'`)
Run: `python3 -m pytest v2/tests/test_connector_delete.py -q`

- [ ] **Step 3: Add the default method** to `BaseConnector` in `v2/core/connectors/base.py` (after `health_check`):
```python
    async def delete_message(self, channel: str | None, message_id: str) -> DeliveryResult:
        """Unsend a previously delivered message. Default: unsupported (send-only platforms
        like GroupMe inherit this). Platforms that CAN delete override it. Never raises —
        returns a DeliveryResult; the deleter maps it to a per-delivery delete_status."""
        return DeliveryResult(False, self.name, channel=channel, message_id=message_id,
                              error="delete unsupported")
```

- [ ] **Step 4: Run it — expect PASS**
Run: `python3 -m pytest v2/tests/test_connector_delete.py -q`

- [ ] **Step 5: Commit**
```bash
git add v2/core/connectors/base.py v2/tests/test_connector_delete.py
git commit -m "feat(connectors): default delete_message (unsupported) on BaseConnector"
```

---

### Task 3: Discord `delete_message` (connector override + adapter)

**Files:**
- Modify: `v2/core/connectors/discord_connector.py` (add override)
- Modify: `v2/integration/discord_client.py` (add `delete_message` to `DiscordClientAdapter`)
- Test: `v2/tests/test_connector_delete.py` (extend)

**Interfaces:**
- Consumes: `BaseConnector.delete_message` (Task 2).
- Produces: `DiscordConnector.delete_message(channel, message_id) -> DeliveryResult(success=True)` on delete or not-found; `DiscordClientAdapter.delete_message(channel, message_id) -> None` (raises on hard error; treats NotFound as success by returning normally).

- [ ] **Step 1: Write the failing tests** (append to `v2/tests/test_connector_delete.py`)
```python
from v2.core.connectors.discord_connector import DiscordConnector

class _FakeDiscordClient:
    def __init__(self, raise_exc=None): self.calls = []; self._raise = raise_exc
    async def delete_message(self, channel, message_id):
        self.calls.append((channel, message_id))
        if self._raise: raise self._raise
    async def ping(self): return True

def test_discord_delete_success():
    client = _FakeDiscordClient()
    r = _run(DiscordConnector(client=client).delete_message("gsa-announcements", "999"))
    assert r.success is True and r.platform == "discord"
    assert client.calls == [("gsa-announcements", "999")]

def test_discord_delete_not_found_is_success():
    # adapter swallows NotFound (message already gone = goal achieved) -> connector reports success
    client = _FakeDiscordClient(raise_exc=None)   # adapter returns normally on not-found (see impl)
    r = _run(DiscordConnector(client=client).delete_message("c", "1"))
    assert r.success is True

def test_discord_delete_hard_error_is_failure():
    client = _FakeDiscordClient(raise_exc=RuntimeError("boom"))
    r = _run(DiscordConnector(client=client).delete_message("c", "1"))
    assert r.success is False and "boom" in (r.error or "")
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError` on `DiscordConnector.delete_message` / client)
Run: `python3 -m pytest v2/tests/test_connector_delete.py -q`

- [ ] **Step 3a: Add the connector override** in `v2/core/connectors/discord_connector.py` (after `_send`):
```python
    async def delete_message(self, channel, message_id) -> DeliveryResult:
        if self.client is None:
            return DeliveryResult(False, self.name, channel=channel, message_id=message_id,
                                  error="discord client not wired")
        try:
            await self.client.delete_message(channel, message_id)
            return DeliveryResult(True, self.name, channel=channel, message_id=message_id)
        except Exception as exc:  # noqa: BLE001
            return DeliveryResult(False, self.name, channel=channel, message_id=message_id,
                                  error=str(exc))
```

- [ ] **Step 3b: Add the adapter method** in `v2/integration/discord_client.py` (after `send_message`):
```python
    async def delete_message(self, channel, message_id):
        import discord
        ch = self._resolve(channel)
        if ch is None:
            raise RuntimeError(f"Discord channel '{channel}' not found")
        try:
            await ch.get_partial_message(int(message_id)).delete()
        except discord.NotFound:
            return  # already gone — goal state achieved, treat as success
```

- [ ] **Step 4: Run — expect PASS**
Run: `python3 -m pytest v2/tests/test_connector_delete.py -q`

- [ ] **Step 5: Commit**
```bash
git add v2/core/connectors/discord_connector.py v2/integration/discord_client.py v2/tests/test_connector_delete.py
git commit -m "feat(discord): delete_message (unsend by id; NotFound=success)"
```

---

### Task 4: Registry — route a deletion to the right connector

**Files:**
- Modify: `v2/core/connectors/registry.py` (add method)
- Test: `v2/tests/test_registry_delete.py` (create)

**Interfaces:**
- Consumes: `BaseConnector.delete_message` (Tasks 2–3).
- Produces: `async ConnectorRegistry.delete_delivery(platform: str, channel: str | None, message_id: str) -> DeliveryResult`. Unknown/disabled platform → `DeliveryResult(False, platform, error="no connector")`.

- [ ] **Step 1: Write the failing test**
```python
# v2/tests/test_registry_delete.py
import sys, asyncio
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path: sys.path.insert(0, str(REPO))
from v2.core.connectors.registry import ConnectorRegistry
from v2.core.connectors.discord_connector import DiscordConnector

def _run(c): return asyncio.get_event_loop().run_until_complete(c)

class _FakeClient:
    async def delete_message(self, channel, message_id): pass
    async def ping(self): return True

def test_registry_routes_delete_to_connector():
    reg = ConnectorRegistry()
    reg.register(DiscordConnector(client=_FakeClient()))
    r = _run(reg.delete_delivery("discord", "chan", "42"))
    assert r.success is True and r.platform == "discord"

def test_registry_delete_unknown_platform():
    r = _run(ConnectorRegistry().delete_delivery("nope", "c", "1"))
    assert r.success is False and "no connector" in (r.error or "").lower()
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: delete_delivery`)
Run: `python3 -m pytest v2/tests/test_registry_delete.py -q`

- [ ] **Step 3: Add the method** in `v2/core/connectors/registry.py` (after `publish`):
```python
    async def delete_delivery(self, platform: str, channel, message_id) -> DeliveryResult:
        """Unsend ONE delivered message via its platform connector. Never raises."""
        connector = self._connectors.get(platform)
        if connector is None or not connector.enabled:
            return DeliveryResult(False, platform, channel=channel, message_id=message_id,
                                  error="no connector")
        try:
            return await connector.delete_message(channel, message_id)
        except Exception as exc:  # noqa: BLE001
            return DeliveryResult(False, platform, channel=channel, message_id=message_id,
                                  error=str(exc))
```

- [ ] **Step 4: Run — expect PASS**
Run: `python3 -m pytest v2/tests/test_registry_delete.py -q`

- [ ] **Step 5: Commit**
```bash
git add v2/core/connectors/registry.py v2/tests/test_registry_delete.py
git commit -m "feat(connectors): registry.delete_delivery routes unsend to the platform connector"
```

---

### Task 5: `PostDeleter.delete_due()`

**Files:**
- Create: `v2/core/publishing/deleter.py`
- Test: `v2/tests/test_post_deleter.py` (create)

**Interfaces:**
- Consumes: `ConnectorRegistry.delete_delivery` (Task 4); the new columns (Task 1).
- Produces: `PostDeleter(conn, registry)`; `async delete_due(self, now: str | None = None) -> dict` returning `{"posts": n, "deleted": d, "unsupported": u, "failed": f}`. Class const `MAX_ATTEMPTS = 5`.

- [ ] **Step 1: Write the failing test**
```python
# v2/tests/test_post_deleter.py
import sys, asyncio, json
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path: sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.connectors.base import DeliveryResult
from v2.core.publishing.deleter import PostDeleter

def _run(c): return asyncio.get_event_loop().run_until_complete(c)

class _FakeRegistry:
    def __init__(self, result_by_platform): self.by = result_by_platform; self.calls = []
    async def delete_delivery(self, platform, channel, message_id):
        self.calls.append((platform, channel, message_id))
        return self.by[platform]

def _seed(conn, delete_at="2000-01-01 00:00:00", status="sent"):
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    conn.execute("INSERT INTO posts(id,org_id,type,content,channels,status,delete_at) "
                 "VALUES(1,1,'worldcup','hi','[\"discord\"]',?,?)", (status, delete_at))
    conn.execute("INSERT INTO post_deliveries(id,post_id,platform,channel,message_id,status) "
                 "VALUES(1,1,'discord','gsa','999','success')")
    conn.commit()

def test_due_post_deletes_discord_and_stamps():
    conn = create_all(":memory:"); _seed(conn)
    reg = _FakeRegistry({"discord": DeliveryResult(True, "discord", message_id="999")})
    out = _run(PostDeleter(conn, reg).delete_due(now="2025-01-01 00:00:00"))
    assert out["deleted"] == 1
    d = conn.execute("SELECT delete_status, deleted_at FROM post_deliveries WHERE id=1").fetchone()
    assert d["delete_status"] == "deleted" and d["deleted_at"] is not None
    p = conn.execute("SELECT deleted_at FROM posts WHERE id=1").fetchone()
    assert p["deleted_at"] is not None
    conn.close()

def test_not_due_is_skipped():
    conn = create_all(":memory:"); _seed(conn, delete_at="2999-01-01 00:00:00")
    reg = _FakeRegistry({"discord": DeliveryResult(True, "discord")})
    out = _run(PostDeleter(conn, reg).delete_due(now="2025-01-01 00:00:00"))
    assert out["posts"] == 0 and reg.calls == []
    conn.close()

def test_unsupported_marks_delivery_not_post_failure():
    conn = create_all(":memory:"); _seed(conn)
    conn.execute("UPDATE post_deliveries SET platform='groupme', message_id='groupme:x:200' WHERE id=1")
    conn.commit()
    reg = _FakeRegistry({"groupme": DeliveryResult(False, "groupme", error="delete unsupported")})
    out = _run(PostDeleter(conn, reg).delete_due(now="2025-01-01 00:00:00"))
    assert out["unsupported"] == 1
    d = conn.execute("SELECT delete_status FROM post_deliveries WHERE id=1").fetchone()
    assert d["delete_status"] == "delete_unsupported"
    conn.close()

def test_deleter_issues_no_DELETE_statements():
    # immortal-records guard: monkeypatch execute to forbid DELETE
    conn = create_all(":memory:"); _seed(conn)
    reg = _FakeRegistry({"discord": DeliveryResult(True, "discord")})
    real = conn.execute
    def guard(sql, *a, **k):
        assert "delete from" not in sql.lower(), f"deleter issued a DELETE: {sql}"
        return real(sql, *a, **k)
    conn.execute = guard
    _run(PostDeleter(conn, reg).delete_due(now="2025-01-01 00:00:00"))
    conn.close()
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: v2.core.publishing.deleter`)
Run: `python3 -m pytest v2/tests/test_post_deleter.py -q`

- [ ] **Step 3: Implement** `v2/core/publishing/deleter.py`:
```python
"""PostDeleter — unsend due posts' delivered messages, marking the DB (records immortal).

Mirror of PostPublisher.publish_due: poll posts with a passed delete_at, route each delivery
to its platform connector, and record a per-delivery delete_status. Never DELETEs a row.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
_REAL = lambda mid: bool(mid) and ":" not in mid and mid != "telegram-broadcast"  # noqa: E731

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
        return summary

    async def _delete_one_post(self, post_id: int, summary: dict) -> None:
        deliveries = self.conn.execute(
            "SELECT id, platform, channel, message_id, status, delete_attempts "
            "FROM post_deliveries WHERE post_id=? AND delete_status IS NULL",
            (post_id,),
        ).fetchall()
        for d in deliveries:
            # nothing was delivered, or no real/deletable id -> nothing to unsend
            if d["status"] != "success" or not _REAL(d["message_id"]):
                self._mark(d["id"], "not_applicable", None, d["delete_attempts"])
                continue
            result = await self.registry.delete_delivery(d["platform"], d["channel"], d["message_id"])
            err = (result.error or "")
            if result.success or "not found" in err.lower() or "unknown message" in err.lower():
                self._mark(d["id"], "deleted", None, d["delete_attempts"]); summary["deleted"] += 1
            elif "unsupported" in err.lower():
                self._mark(d["id"], "delete_unsupported", err, d["delete_attempts"]); summary["unsupported"] += 1
            elif d["delete_attempts"] + 1 >= MAX_ATTEMPTS:
                self._mark(d["id"], "delete_failed", err, d["delete_attempts"] + 1); summary["failed"] += 1
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
            "UPDATE post_deliveries SET delete_status=?, deleted_at=?, delete_error=?, delete_attempts=? "
            "WHERE id=?",
            (status, _now() if status == "deleted" else None, error, attempts, delivery_id))
        self.conn.commit()
```

- [ ] **Step 4: Run — expect PASS** (all 4 tests)
Run: `python3 -m pytest v2/tests/test_post_deleter.py -q`

- [ ] **Step 5: Commit**
```bash
git add v2/core/publishing/deleter.py v2/tests/test_post_deleter.py
git commit -m "feat(publishing): PostDeleter.delete_due — unsend due posts, mark per-delivery (records immortal)"
```

---

### Task 6: Wire `delete_due()` into the scheduler tick

**Files:**
- Modify: `v2/core/publishing/scheduler.py` (`Scheduler.__init__` + `tick`)
- Modify: `v2/integration/scheduler_runner.py` (`start` builds the deleter)
- Test: `v2/tests/test_scheduler_delete_tick.py` (create)

**Interfaces:**
- Consumes: `PostDeleter` (Task 5).
- Produces: `Scheduler.tick()` calls `delete_due()` AFTER `publish_due()`; its counts merge into the tick result under `"deleted"`.

- [ ] **Step 1: Write the failing test**
```python
# v2/tests/test_scheduler_delete_tick.py
import sys, asyncio
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path: sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.connectors.base import DeliveryResult
from v2.core.publishing.scheduler import Scheduler

def _run(c): return asyncio.get_event_loop().run_until_complete(c)

class _Pub:
    async def publish_due(self, now): return {"published": 0, "sent": 0, "failed": 0}
class _Reg:
    async def delete_delivery(self, p, c, m): return DeliveryResult(True, p, message_id=m)

def test_tick_runs_delete_due():
    conn = create_all(":memory:")
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'N','njit','university')")
    conn.execute("INSERT INTO posts(id,org_id,type,content,channels,status,delete_at) "
                 "VALUES(1,1,'worldcup','x','[\"discord\"]','sent','2000-01-01 00:00:00')")
    conn.execute("INSERT INTO post_deliveries(id,post_id,platform,channel,message_id,status) "
                 "VALUES(1,1,'discord','c','999','success')")
    conn.commit()
    sch = Scheduler(conn, _Pub(), registry=_Reg())
    out = _run(sch.tick())
    assert out["deleted"] == 1
    conn.close()
```

- [ ] **Step 2: Run — expect FAIL** (`TypeError` — `Scheduler` has no `registry` kwarg / no `deleted` key)
Run: `python3 -m pytest v2/tests/test_scheduler_delete_tick.py -q`

- [ ] **Step 3a: Modify `Scheduler`** in `v2/core/publishing/scheduler.py`. Add to `__init__` (alongside `self.conn`, `self.publisher`):
```python
    def __init__(self, conn, publisher, registry=None):
        self.conn = conn
        self.publisher = publisher
        from v2.core.publishing.deleter import PostDeleter
        self.deleter = PostDeleter(conn, registry) if registry is not None else None
```
In `tick()`, after the `published = await self.publisher.publish_due(...)` line and before building `result`:
```python
        deleted = await self.deleter.delete_due(now_dt.strftime(_FMT)) if self.deleter else {}
```
and merge into the result dict:
```python
        result = {"templates_materialized": templates,
                  "reminders_materialized": reminders, **published,
                  "deleted": deleted.get("deleted", 0),
                  "delete_unsupported": deleted.get("unsupported", 0),
                  "delete_failed": deleted.get("failed", 0)}
```
(Keep the existing `__init__` body that builds whatever else it had — only add `registry`/`deleter`.)

- [ ] **Step 3b: Modify `SchedulerRunner.start`** in `v2/integration/scheduler_runner.py` — pass the registry:
```python
        self._scheduler = Scheduler(self._conn, publisher, registry=self.registry)
```

- [ ] **Step 4: Run — expect PASS**
Run: `python3 -m pytest v2/tests/test_scheduler_delete_tick.py -q`

- [ ] **Step 5: Commit**
```bash
git add v2/core/publishing/scheduler.py v2/integration/scheduler_runner.py v2/tests/test_scheduler_delete_tick.py
git commit -m "feat(publishing): run PostDeleter.delete_due after publish_due each scheduler tick"
```

---

### Task 7: `delete_at` on `PostDraft` + `enqueue_post`

**Files:**
- Modify: `v2/core/publishing/sources.py` (`PostDraft` dataclass + `enqueue_post` INSERT ~line 149)
- Test: `v2/tests/test_enqueue_delete_at.py` (create)

**Interfaces:**
- Produces: `PostDraft.delete_at: str | None = None`; `enqueue_post` persists it into `posts.delete_at`.

- [ ] **Step 1: Write the failing test**
```python
# v2/tests/test_enqueue_delete_at.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path: sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.publishing.sources import PostDraft, enqueue_post

def test_enqueue_persists_delete_at():
    conn = create_all(":memory:")
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'N','njit','university')")
    conn.commit()
    pid = enqueue_post(conn, PostDraft(org_id=1, content="hi", channels=["discord"],
                                       delete_at="2026-06-25 00:00:00"))
    row = conn.execute("SELECT delete_at FROM posts WHERE id=?", (pid,)).fetchone()
    assert row["delete_at"] == "2026-06-25 00:00:00"
    conn.close()
```

- [ ] **Step 2: Run — expect FAIL** (`TypeError: unexpected keyword 'delete_at'`)
Run: `python3 -m pytest v2/tests/test_enqueue_delete_at.py -q`

- [ ] **Step 3a: Add the field** to `PostDraft` (after `scheduled_for`):
```python
    delete_at: str | None = None              # UTC "YYYY-MM-DD HH:MM:SS"; None = keep forever
```

- [ ] **Step 3b: Add it to the INSERT** in `enqueue_post` (the columns/values ~line 149):
```python
        "INSERT INTO posts(org_id, type, title, content, channels, discord_channel, "
        "scheduled_for, delete_at, status, source_type, source_id, metadata, created_by) "
        "VALUES (?,?,?,?,?,?,?,?,'scheduled',?,?,?,?)",
        (draft.org_id, draft.type, draft.title, content,
         json.dumps(draft.channels or []), draft.discord_channel, draft.scheduled_for,
         draft.delete_at, draft.source_type, draft.source_id, meta_json, draft.created_by),
```

- [ ] **Step 4: Run — expect PASS**
Run: `python3 -m pytest v2/tests/test_enqueue_delete_at.py -q`

- [ ] **Step 5: Commit**
```bash
git add v2/core/publishing/sources.py v2/tests/test_enqueue_delete_at.py
git commit -m "feat(publishing): PostDraft.delete_at + persist it in enqueue_post"
```

---

### Task 8: Dashboard — "delete after / keep until" on the create-post form

**Files:**
- Modify: `v2/local_server.py` (the `POST /posts` handler — accept `delete_at`)
- Modify: `dashboard/app.js` (the create-post form — add the input; send `delete_at`)
- Modify: `dashboard/index.html` (form field) — only if the form is in HTML, else app.js builds it
- Test: `v2/tests/test_local_server_delete_at.py` (create) — handler-level (the existing `test_local_server.py` HTTP tests fail with 403 in this sandbox, so test the handler's post-insert path directly with an in-memory DB, mirroring how other handler logic is unit-tested).

**Interfaces:**
- Consumes: `posts.delete_at` (Task 1), `enqueue_post`/insert (Task 7).
- Produces: a `POST /posts` body field `delete_at` (UTC string or empty) stored on the row.

- [ ] **Step 1: Write the failing test** — locate the function in `v2/local_server.py` that handles `POST /posts` (grep `def ` near the `/posts` route; it builds the INSERT or calls a create helper). Test that, given a payload with `delete_at`, the inserted row carries it. Example (adapt the import to the actual handler/helper name found):
```python
# v2/tests/test_local_server_delete_at.py
import sys, json
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path: sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.local_server import _create_post   # <-- replace with the real helper name found by grep

def test_create_post_stores_delete_at():
    conn = create_all(":memory:")
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'N','njit','university')")
    conn.commit()
    pid = _create_post(conn, {"org_id": 1, "type": "broadcast", "content": "hi",
                              "channels": ["discord"], "delete_at": "2026-06-25 00:00:00"})
    row = conn.execute("SELECT delete_at FROM posts WHERE id=?", (pid,)).fetchone()
    assert row["delete_at"] == "2026-06-25 00:00:00"
    conn.close()
```
(If `POST /posts` inserts inline rather than via a helper, first refactor the insert into a small `_create_post(conn, payload) -> int` helper as part of this task — that is the testable unit — then call it from the handler.)

- [ ] **Step 2: Run — expect FAIL** (ImportError / `delete_at` ignored)
Run: `python3 -m pytest v2/tests/test_local_server_delete_at.py -q`

- [ ] **Step 3a: Backend** — in `v2/local_server.py`, read `delete_at` from the JSON body (default `None`/empty→`None`) and include it in the posts INSERT (mirror the `scheduled_for` handling). Validate it's a `YYYY-MM-DD HH:MM:SS` string or empty; empty → `None`.

- [ ] **Step 3b: Frontend** — in `dashboard/app.js` create-post form: add a field labeled "Auto-delete (keep until)" — a datetime-local input (and/or quick presets like "24h"/"7d" that compute a UTC datetime). On submit, include `delete_at` (UTC string) in the POST body; empty when blank.

- [ ] **Step 4: Run — expect PASS**
Run: `python3 -m pytest v2/tests/test_local_server_delete_at.py -q`
Manual check: dashboard → create a post with a near-future delete time → confirm the row's `delete_at` is set (sql.js view or a quick `SELECT`).

- [ ] **Step 5: Commit**
```bash
git add v2/local_server.py dashboard/app.js dashboard/index.html v2/tests/test_local_server_delete_at.py
git commit -m "feat(dashboard): set a post's auto-delete time (delete_at) on create"
```

---

### Task 9: Phase 0 retro follow-ups (persistence + media id + channel doc)

**Files:**
- Test: `v2/tests/test_telegram_message_id.py` (extend) and/or `v2/tests/test_connectors.py`
- (Doc already done in Task 1's `channel` comment.)

**Interfaces:** none new — coverage only.

- [ ] **Step 1: Write the tests**
  - Registry persistence: build a `ConnectorRegistry(conn=create_all(":memory:"))`, register a fake-client `TelegramConnector` returning `(42, -100)`, publish a `Post(id=…)` to telegram, then assert the `post_deliveries` row has `message_id='42'` and `channel='-100'`.
  - Media path id: `TelegramConnector(client=_FakeClient((7, -5))).send_media("x","/tmp/p.png","c")` → `DeliveryResult.message_id=='7'`, `channel=='-5'`.

- [ ] **Step 2: Run — expect FAIL** (if any gap) / **PASS** if behavior already correct (these guard, may pass immediately — note that in the commit).
Run: `python3 -m pytest v2/tests/test_telegram_message_id.py v2/tests/test_connectors.py -q`

- [ ] **Step 3: (only if a test fails)** fix the minimal code; otherwise none.

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**
```bash
git add v2/tests/test_telegram_message_id.py v2/tests/test_connectors.py
git commit -m "test(publishing): registry persists real telegram id+chat; media path id capture (Phase 0 follow-ups)"
```

---

## Final integration

- [ ] Run the full publishing/connector/scheduler suite:
`python3 -m pytest v2/tests/test_deletion_schema.py v2/tests/test_connector_delete.py v2/tests/test_registry_delete.py v2/tests/test_post_deleter.py v2/tests/test_scheduler_delete_tick.py v2/tests/test_enqueue_delete_at.py v2/tests/test_local_server_delete_at.py v2/tests/test_telegram_message_id.py v2/tests/test_connectors.py v2/tests/test_publisher.py bot/tests/test_worldcup.py -q`
- [ ] Confirm no regressions in the broader suite (`v2/tests` + `bot/tests`), isolating any pre-existing failures.
- [ ] Show the full diff to the owner; on sign-off, commit is already done per-task — restart bots (`bash scripts/restart.sh`) so the scheduler runs `delete_due`. DB columns are additive (idempotent `create_all` on startup).

## Self-review notes
- Spec coverage: schema (T1), connector default+Discord (T2,T3), registry routing (T4), PostDeleter w/ 404=success, per-delivery state, bounded retries, rollup, immortal guard (T5), scheduler wiring after publish (T6), enqueue/code path (T7), dashboard (T8), Phase 0 follow-ups + channel doc (T1,T9). Telegram deletion + 48h guard = **Phase 2, explicitly out of this plan**.
- Deferred (flagged): Phase 2 (Telegram unsend), cancel/extend pending deletions, reminder auto-generation, chunked multi-id deletion.
