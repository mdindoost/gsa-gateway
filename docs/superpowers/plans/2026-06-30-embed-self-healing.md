# Embed Self-Healing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `embed_chunks.py` self-healing — a plain re-run (no `--force`) backfills any active-parent chunk lacking a vector, transient Ollama drops are retried, persistent failures are reported with a non-zero exit, and orphan chunk rows are GC'd.

**Architecture:** Change the resumable unit from "items without chunks" to "chunks without a vector" (the exact complement of invariant condition 2 → convergence-by-complement). Add a shared retry-policy free function. Add an orphan chunk-ROW GC. Align `embed_all` to the shared retry policy.

**Tech Stack:** Python 3.11, Ollama `nomic-embed-text`, sqlite-vec (`knowledge_chunk_vectors` vec0), pytest.

## Global Constraints

- Crawl/embed are data-bringing only; this change writes vectors + GCs derived chunk rows, never edits human-readable content.
- Never insert `search_text` (generated column). Embeddings: `search_document: ` doc prefix (via `descriptor.doc_prefix` for chunks; `embed_all` keeps its own `search_document: …[:2000]`), L2-normalized.
- Caller owns the transaction; `vector_gc` sweeps do NOT commit.
- `embed_with_retry` returns the **RAW** vector; the **caller normalizes once** at its single write site (no double-normalize).
- `embed_chunks` retries the **exact `embed_input`** the batch built (`descriptor.doc_prefix + descriptor.truncate_to_tokens(text, descriptor.context_window)`), via `_embed` — never `embed_document` (which char-truncates at 2000).
- The Phase-2 coverage select is intentionally **model-blind** (= complement of condition 2). Model changes remain the `--force` path; condition 4 (stale `model_id`) + condition 1 are the backstops.
- On incomplete coverage / outage: **report + non-zero exit** (never silent, never an uncaught traceback). This is load-bearing: serving caches `corpus_ready()`→`assert_chunk_invariant` per process; a restart on a holed corpus silently disables deep-fallback.
- Spec: `docs/superpowers/specs/2026-06-30-embed-self-healing-design.md`.

## File Structure

- **Modify** `v2/core/retrieval/embedder.py` — add module-level free function `embed_with_retry`.
- **Modify** `v2/core/database/vector_gc.py` — add `count_orphan_chunk_rows` + `sweep_orphan_chunk_rows`.
- **Modify** `v2/scripts/embed_chunks.py` — extract a testable `run_chunk_embed(conn, d, emb, …)`; rework `main` (health-check, coverage-driven embed, batch-exception degrade, per-slot retry, outage-abort, GC, report, exit code).
- **Modify** `v2/scripts/embed_all.py` — use `embed_with_retry` for its per-item retry.
- **Create** `v2/tests/test_embed_self_healing.py`.

---

### Task 1: `embed_with_retry` retry-policy free function

**Files:**
- Modify: `v2/core/retrieval/embedder.py`
- Test: `v2/tests/test_embed_self_healing.py`

**Interfaces:**
- Produces: `embed_with_retry(call, attempts=3, backoff=0.5) -> list[float] | None` — module-level free function in `embedder.py`. `call` is a zero-arg callable returning a RAW embedding (`list[float] | None`). Returns the first non-`None` result; retries on `None` or exception (sleeping `backoff * attempt`); returns `None` after `attempts`. Never raises. Does NOT normalize.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_embed_self_healing.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.retrieval.embedder import embed_with_retry


def test_embed_with_retry_first_try_no_extra_calls():
    calls = []
    def call():
        calls.append(1); return [0.1, 0.2]
    assert embed_with_retry(call, attempts=3, backoff=0) == [0.1, 0.2]
    assert len(calls) == 1  # stopped at first success


def test_embed_with_retry_none_then_success():
    seq = [None, [1.0]]
    assert embed_with_retry(lambda: seq.pop(0), attempts=3, backoff=0) == [1.0]


def test_embed_with_retry_all_none_returns_none_after_attempts():
    calls = []
    def call():
        calls.append(1); return None
    assert embed_with_retry(call, attempts=3, backoff=0) is None
    assert len(calls) == 3  # exactly `attempts` tries


def test_embed_with_retry_exception_then_success_never_raises():
    seq = [RuntimeError("conn reset"), [2.0]]
    def call():
        x = seq.pop(0)
        if isinstance(x, Exception):
            raise x
        return x
    assert embed_with_retry(call, attempts=3, backoff=0) == [2.0]


def test_embed_with_retry_exception_every_time_returns_none():
    def call():
        raise TimeoutError("down")
    assert embed_with_retry(call, attempts=2, backoff=0) is None  # no raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest v2/tests/test_embed_self_healing.py -k embed_with_retry -v`
Expected: FAIL — `cannot import name 'embed_with_retry'`.

- [ ] **Step 3: Implement** (append to `v2/core/retrieval/embedder.py`, after the `Embedder` class)

```python
import time  # add to the existing imports at top of the file if not present


def embed_with_retry(call, attempts: int = 3, backoff: float = 0.5):
    """Retry/backoff policy wrapper around a RAW embed callable.

    `call` is a zero-arg callable returning ``list[float] | None`. Returns the first
    non-None result; on None or exception, sleeps ``backoff * attempt`` and retries up to
    ``attempts`` total. Returns None after the last attempt. Never raises. Does NOT
    normalize — the caller normalizes once at its write site.
    """
    for attempt in range(1, attempts + 1):
        try:
            vec = call()
            if vec is not None:
                return vec
        except Exception:  # noqa: BLE001 - transient timeout/conn reset; retry then give up
            pass
        if attempt < attempts and backoff:
            time.sleep(backoff * attempt)
    return None
```
(`import time` at the top of `embedder.py` if it isn't already imported.)

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest v2/tests/test_embed_self_healing.py -k embed_with_retry -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/embedder.py v2/tests/test_embed_self_healing.py
git commit -m "feat(embed): embed_with_retry retry-policy helper"
```

---

### Task 2: orphan chunk-ROW GC in `vector_gc.py`

**Files:**
- Modify: `v2/core/database/vector_gc.py`
- Test: `v2/tests/test_embed_self_healing.py`

**Interfaces:**
- Produces: `count_orphan_chunk_rows(conn) -> int` and `sweep_orphan_chunk_rows(conn) -> int` — count/delete `knowledge_chunks` whose parent item is inactive or missing. Caller owns the txn (no commit). Returns the deleted count.

- [ ] **Step 1: Write the failing test**

```python
import struct
from v2.core.database.schema import create_all
from v2.core.retrieval.model_descriptor import active_descriptor

_D = active_descriptor()

def _vec_bytes():
    return struct.pack(f"{_D.dim}f", *([0.0] * _D.dim))

def _conn_with_chunks():
    conn = create_all(":memory:")
    conn.execute("INSERT OR IGNORE INTO organizations(id,name,slug,type) VALUES (1,'A','a','office')")
    # active parent + chunk
    conn.execute("INSERT INTO knowledge_items(id,org_id,type,content,is_active) VALUES (10,1,'policy','x',1)")
    conn.execute("INSERT INTO knowledge_chunks(id,parent_id,source_key,ordinal,text,content_hash,model_id) "
                 "VALUES (100,10,'item:10',0,'x','h',?)", (_D.id,))
    # inactive parent + chunk (orphan row)
    conn.execute("INSERT INTO knowledge_items(id,org_id,type,content,is_active) VALUES (11,1,'policy','y',0)")
    conn.execute("INSERT INTO knowledge_chunks(id,parent_id,source_key,ordinal,text,content_hash,model_id) "
                 "VALUES (101,11,'item:11',0,'y','h',?)", (_D.id,))
    return conn

def test_sweep_orphan_chunk_rows_deletes_inactive_parent_only():
    from v2.core.database.vector_gc import count_orphan_chunk_rows, sweep_orphan_chunk_rows
    conn = _conn_with_chunks()
    assert count_orphan_chunk_rows(conn) == 1
    assert sweep_orphan_chunk_rows(conn) == 1
    assert count_orphan_chunk_rows(conn) == 0
    # active-parent chunk untouched
    assert conn.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE id=100").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE id=101").fetchone()[0] == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest v2/tests/test_embed_self_healing.py -k orphan_chunk_rows -v`
Expected: FAIL — `cannot import name 'count_orphan_chunk_rows'`.

- [ ] **Step 3: Implement** (add to `v2/core/database/vector_gc.py`, near the other `_*_ORPHANS` helpers)

```python
_CHUNK_ROW_ORPHANS = """
    SELECT c.id FROM knowledge_chunks c
    LEFT JOIN knowledge_items i ON i.id = c.parent_id AND i.is_active = 1
    WHERE i.id IS NULL
"""


def count_orphan_chunk_rows(conn: sqlite3.Connection) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM ({_CHUNK_ROW_ORPHANS})").fetchone()[0]


def sweep_orphan_chunk_rows(conn: sqlite3.Connection) -> int:
    ids = [r[0] for r in conn.execute(_CHUNK_ROW_ORPHANS)]
    conn.executemany("DELETE FROM knowledge_chunks WHERE id = ?", [(i,) for i in ids])
    return len(ids)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest v2/tests/test_embed_self_healing.py -k orphan_chunk_rows -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add v2/core/database/vector_gc.py v2/tests/test_embed_self_healing.py
git commit -m "feat(vector_gc): orphan chunk-row GC (count/sweep_orphan_chunk_rows)"
```

---

### Task 3: rework `embed_chunks.py` — coverage-driven, self-healing, retry, GC, exit code

**Files:**
- Modify: `v2/scripts/embed_chunks.py`
- Test: `v2/tests/test_embed_self_healing.py`

**Interfaces:**
- Consumes: `embed_with_retry` (Task 1); `sweep_orphan_chunk_rows` (Task 2); existing `chunk_text`, `drop_item_chunks`, `content_hash`, `active_descriptor`, `Embedder`, `vector_gc.sweep_orphan_chunk_vectors`/`sweep_orphan_item_vectors`/`assert_chunk_invariant`, `DEFAULT_EXCLUDE_TYPES`.
- Produces: `run_chunk_embed(conn, d, emb, *, batch=BATCH, attempts=3, backoff=0.5, force=False, limit=None) -> dict` returning `{"chunked": int, "vectors": int, "retried": int, "failed": int, "aborted": bool, "starting_holes": int}`. It does Phase 1 (create chunk rows for items lacking current-model chunks) + Phase 2 (embed all active-parent unvectored chunks, batched, per-slot retry on None/batch-exception, outage-abort), per-batch commit. It does NOT GC, assert, or exit — `main` owns those. `main(argv=None, emb=None)` wires health-check, `run_chunk_embed`, GC, report, and the exit code; `emb` is injectable for tests.

- [ ] **Step 1: Write the failing tests** (convergence, batch-exception degrade, retry-input consistency, no-masking)

```python
import struct, pytest
from v2.core.database.schema import create_all
from v2.core.retrieval.model_descriptor import active_descriptor
_D = active_descriptor()

def _norm_bytes():
    import sqlite_vec
    return sqlite_vec.serialize_float32([0.0] * _D.dim)

class FakeEmb:
    """Fake embedder. Returns a fixed vector except for texts whose underlying chunk text is in
    `fail_texts` — those return None in BOTH _embed and _embed_batch (so the in-run retry can't
    heal them). raise_batch=True makes _embed_batch raise once (connection reset)."""
    def __init__(self, fail_texts=(), raise_batch=False):
        self.fail_texts = set(fail_texts)
        self.raise_batch = raise_batch
        self.embed_inputs = []  # records every single-call input (for C1 consistency)
    def _fails(self, prepared):
        return any(ft in prepared for ft in self.fail_texts)
    def _embed(self, text, timeout=30):
        self.embed_inputs.append(text)
        return None if self._fails(text) else [1.0] * _D.dim
    def _embed_batch(self, texts, timeout=60):
        if self.raise_batch:
            self.raise_batch = False
            raise ConnectionResetError("ollama dropped")
        return [None if self._fails(t) else [1.0] * _D.dim for t in texts]
    def health_check(self):
        return True

def _seed_item(conn, iid, content):
    conn.execute("INSERT OR IGNORE INTO organizations(id,name,slug,type) VALUES (1,'A','a','office')")
    conn.execute("INSERT INTO knowledge_items(id,org_id,type,content,is_active) VALUES (?,1,'policy',?,1)",
                 (iid, content))

def test_run_chunk_embed_converges_without_force():
    from v2.scripts.embed_chunks import run_chunk_embed
    from v2.core.database.vector_gc import assert_chunk_invariant
    conn = create_all(":memory:")
    _seed_item(conn, 1, "alpha content one")
    _seed_item(conn, 2, "bravo content two")
    # First run: fake drops item-2's chunk(s) in both paths → a residual hole.
    res = run_chunk_embed(conn, _D, FakeEmb(fail_texts=["bravo content two"]), batch=8, backoff=0)
    assert res["failed"] >= 1
    with pytest.raises(AssertionError):
        assert_chunk_invariant(conn, _D)
    # Re-run with a healthy fake, NO --force → backfills the hole, invariant passes.
    res2 = run_chunk_embed(conn, _D, FakeEmb(), batch=8, backoff=0)
    assert res2["failed"] == 0
    assert_chunk_invariant(conn, _D)  # no raise

def test_run_chunk_embed_batch_exception_degrades_not_crash():
    from v2.scripts.embed_chunks import run_chunk_embed
    conn = create_all(":memory:")
    _seed_item(conn, 1, "gamma content")
    res = run_chunk_embed(conn, _D, FakeEmb(raise_batch=True), batch=8, backoff=0)
    # batch raised once → degraded to per-slot retry → chunk(s) embedded, no traceback, failed=0
    assert res["failed"] == 0 and res["vectors"] >= 1

def test_run_chunk_embed_retry_uses_exact_batch_input():
    from v2.scripts.embed_chunks import run_chunk_embed
    conn = create_all(":memory:")
    _seed_item(conn, 1, "delta content")
    fake = FakeEmb(raise_batch=True)  # forces the single-retry path for every slot
    run_chunk_embed(conn, _D, fake, batch=8, backoff=0)
    # every single-retry input carries the descriptor doc prefix (proves it's the prepared embed_input,
    # not a re-prefixed embed_document call)
    assert fake.embed_inputs and all(s.startswith(_D.doc_prefix) for s in fake.embed_inputs)

def test_run_chunk_embed_no_masking_of_stale_model():
    from v2.scripts.embed_chunks import run_chunk_embed
    from v2.core.database.vector_gc import assert_chunk_invariant
    conn = create_all(":memory:")
    _seed_item(conn, 1, "epsilon content")
    # pre-insert a STALE-model chunk for item 1 (so Phase 1 sees it 'has a chunk' and skips it)
    conn.execute("INSERT INTO knowledge_chunks(id,parent_id,source_key,ordinal,text,content_hash,model_id) "
                 "VALUES (500,1,'item:1',0,'epsilon content','h','OLD-MODEL')")
    conn.commit()
    run_chunk_embed(conn, _D, FakeEmb(), batch=8, backoff=0)  # writes a current-model vector for chunk 500
    with pytest.raises(AssertionError):   # condition 4 still fires on the stale model_id
        assert_chunk_invariant(conn, _D)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest v2/tests/test_embed_self_healing.py -k run_chunk_embed -v`
Expected: FAIL — `cannot import name 'run_chunk_embed'`.

- [ ] **Step 3: Implement** — replace the body of `v2/scripts/embed_chunks.py` from `BATCH = 64` through `main()` with:

```python
import time

BATCH = 64


def _coverage_holes(conn):
    """Active-parent chunks with no vector — the EXACT complement of invariant condition 2,
    so embedding all of these (successfully) drives condition 2 to 0 (convergence-by-complement).
    Model-blind by design; condition 4 (stale model_id) is the backstop."""
    return conn.execute(
        """
        SELECT c.id, c.text, i.org_id, i.type, c.parent_id
        FROM knowledge_chunks c JOIN knowledge_items i ON i.id = c.parent_id
        WHERE i.is_active = 1
          AND NOT EXISTS (SELECT 1 FROM knowledge_chunk_vectors cv WHERE cv.chunk_id = c.id)
        ORDER BY c.id
        """
    ).fetchall()


def _prepare(d, text):
    return d.doc_prefix + d.truncate_to_tokens(text, d.context_window)


def _write_vector(conn, chunk_id, raw, org_id, typ, parent_id) -> bool:
    norm = Embedder.normalize(raw)
    if norm is None:
        return False
    conn.execute(
        "INSERT INTO knowledge_chunk_vectors(chunk_id, embedding, org_id, type, parent_id) "
        "VALUES (?,?,?,?,?)",
        (chunk_id, sqlite_vec.serialize_float32(norm), org_id, typ, parent_id),
    )
    return True


def run_chunk_embed(conn, d, emb, *, batch=BATCH, attempts=3, backoff=0.5,
                    force=False, limit=None) -> dict:
    """Phase 1: chunk items lacking a current-model chunk. Phase 2: embed every active-parent
    unvectored chunk (new + previously-failed), batched, with per-slot retry on None/batch-exception
    and an outage-abort. Per-batch commit (durable progress). No GC / assert / exit here — main owns
    those. Returns counts + an `aborted` flag."""
    from v2.core.retrieval.embedder import embed_with_retry

    # ── Phase 1: create chunk rows for items that have no current-model chunk ──
    exclude = tuple(DEFAULT_EXCLUDE_TYPES)
    ph = ",".join("?" * len(exclude))
    sql = (f"SELECT id, content FROM knowledge_items WHERE is_active=1 AND type NOT IN ({ph})")
    params = list(exclude)
    if not force:
        # NON-model-scoped (original behavior): an item with only stale-model chunks is treated as
        # "has chunks" and skipped, so the stale rows survive for condition 4 to catch — model
        # changes are the --force path, not a plain re-run. Do NOT add a model_id filter here.
        sql += " AND id NOT IN (SELECT DISTINCT parent_id FROM knowledge_chunks)"
    sql += " ORDER BY id"
    items = conn.execute(sql, params).fetchall()
    if limit:
        items = items[:limit]
    chunked = 0
    for r in items:
        drop_item_chunks(conn, r["id"])
        for ordinal, ch in enumerate(chunk_text(r["content"] or "", d)):
            conn.execute(
                "INSERT INTO knowledge_chunks(parent_id, source_key, ordinal, text, content_hash, model_id) "
                "VALUES (?,?,?,?,?,?)",
                (r["id"], f"item:{r['id']}", ordinal, ch, content_hash(ch, d.id), d.id),
            )
            chunked += 1
    conn.commit()

    # ── Phase 2: embed all active-parent unvectored chunks (coverage-driven) ──
    holes = _coverage_holes(conn)
    starting_holes = len(holes)
    vectors = retried = failed = 0
    aborted = False
    for i in range(0, len(holes), batch):
        chunk_batch = holes[i:i + batch]
        inputs = [_prepare(d, c["text"]) for c in chunk_batch]
        try:
            vecs = emb._embed_batch(inputs)
        except Exception:  # noqa: BLE001 - C2: batch-level conn reset → degrade to per-slot retry
            vecs = [None] * len(chunk_batch)
        batch_written = 0
        for c, prepared, raw in zip(chunk_batch, inputs, vecs):
            if raw is None:
                retried += 1
                raw = embed_with_retry(lambda p=prepared: emb._embed(p), attempts=attempts, backoff=backoff)
            if raw is not None and _write_vector(conn, c["id"], raw, c["org_id"], c["type"], c["parent_id"]):
                vectors += 1
                batch_written += 1
            else:
                failed += 1
        conn.commit()
        if chunk_batch and batch_written == 0:   # N1: a whole batch failed even after retries → outage
            aborted = True
            break
    return {"chunked": chunked, "vectors": vectors, "retried": retried,
            "failed": failed, "aborted": aborted, "starting_holes": starting_holes}


def main(argv=None, emb=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="Re-chunk all (else only items with no current-model chunk).")
    args = ap.parse_args(argv)

    d = active_descriptor()
    if emb is None:
        emb = Embedder(model=d.ollama_name)
        if not emb.health_check():               # fast-fail before any DB write
            print("ERROR: embedder health check failed (Ollama/model unavailable).")
            return 2
    conn = get_connection(args.db)

    res = run_chunk_embed(conn, d, emb, force=args.force, limit=args.limit)

    if res["aborted"]:
        print(f"ABORTED (outage): chunked={res['chunked']} vectors={res['vectors']} "
              f"retried={res['retried']} failed={res['failed']} starting_holes={res['starting_holes']} "
              f"— progress committed; re-run when Ollama is healthy.")
        return 1

    swept = (vector_gc.sweep_orphan_chunk_vectors(conn)
             + vector_gc.sweep_orphan_item_vectors(conn)
             + vector_gc.sweep_orphan_chunk_rows(conn))
    conn.commit()
    print(f"chunked={res['chunked']} vectors={res['vectors']} retried={res['retried']} "
          f"failed={res['failed']} starting_holes={res['starting_holes']} gc_swept={swept}", flush=True)

    if res["failed"] > 0:
        print(f"INCOMPLETE: {res['failed']} chunk(s) still unvectored — re-run to converge (no --force needed).")
        return 1

    vector_gc.assert_chunk_invariant(conn, d)
    print(f"DONE; invariant OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Also ensure the imports at the top of `embed_chunks.py` still include: `argparse, sys, time, sqlite_vec`, `get_connection`, `vector_gc`, `chunk_text`, `drop_item_chunks, content_hash`, `active_descriptor`, `Embedder`, `DEFAULT_EXCLUDE_TYPES`. (They already do except `time`; add `import time`.)

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest v2/tests/test_embed_self_healing.py -k run_chunk_embed -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the full new test file + chunk regression suite**

Run: `python3 -m pytest v2/tests/test_embed_self_healing.py v2/tests/test_chunk_invariant.py v2/tests/test_vector_gc.py v2/tests/test_chunk_populate.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add v2/scripts/embed_chunks.py v2/tests/test_embed_self_healing.py
git commit -m "feat(embed_chunks): coverage-driven self-healing + retry + GC + exit code"
```

---

### Task 4: align `embed_all.py` to the shared retry policy

**Files:**
- Modify: `v2/scripts/embed_all.py:171-188` (the per-item embed loop in `run_embedding`)
- Test: `v2/tests/test_embed_self_healing.py`

**Interfaces:**
- Consumes: `embed_with_retry` (Task 1). embed_all keeps its own module-level `embed_document`/`_store_vector`; only the retry POLICY is shared. `_store_vector` normalizes once (no double-normalize, since `embed_with_retry` returns the raw vector).

- [ ] **Step 1: Write the failing test**

```python
def test_embed_all_uses_retry_then_succeeds(monkeypatch):
    import v2.scripts.embed_all as ea
    from v2.core.database.schema import create_all
    conn = create_all(":memory:")
    conn.execute("INSERT OR IGNORE INTO organizations(id,name,slug,type) VALUES (1,'A','a','office')")
    conn.execute("INSERT INTO knowledge_items(id,org_id,type,title,content,is_active) "
                 "VALUES (1,1,'policy','t','zeta content',1)")
    conn.commit()
    calls = {"n": 0}
    def flaky(text, timeout=30):
        calls["n"] += 1
        return None if calls["n"] == 1 else [1.0] * 768
    monkeypatch.setattr(ea, "embed_document", flaky)
    succeeded, failed, total = ea.run_embedding(conn, force=False, single=None)
    assert succeeded == 1 and failed == []
    assert calls["n"] == 2  # first None, retried once → success
    assert conn.execute("SELECT COUNT(*) FROM knowledge_vectors WHERE item_id=1").fetchone()[0] == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest v2/tests/test_embed_self_healing.py -k embed_all_uses_retry -v`
Expected: FAIL — currently embed_all's inline loop calls `embed_document` directly; the test asserts the retry path goes through `embed_with_retry` semantics. (If it already passes due to the existing 2-attempt loop, KEEP the test and still do Step 3 to route through the shared helper for consistency — the test should remain green.)

- [ ] **Step 3: Implement** — in `v2/scripts/embed_all.py`, replace the inline retry block (the `vec = None` / `for attempt in (1, 2): …` lines inside `run_embedding`) with:

```python
        from v2.core.retrieval.embedder import embed_with_retry  # top-of-file import preferred
        vec = embed_with_retry(lambda r=row: embed_document(r["search_text"]))
```
(Move the import to the top of the file with the other imports. `embed_document` and `_store_vector` are unchanged; `_store_vector` still normalizes the returned raw vector once.)

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest v2/tests/test_embed_self_healing.py -k embed_all_uses_retry -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add v2/scripts/embed_all.py v2/tests/test_embed_self_healing.py
git commit -m "feat(embed_all): share embed_with_retry policy (no double-normalize)"
```

---

### Task 5: dev-copy validation (no live write)

**Files:** none (operational).

- [ ] **Step 1: Full new suite + regressions green**

Run: `python3 -m pytest v2/tests/test_embed_self_healing.py v2/tests/test_chunk_invariant.py v2/tests/test_vector_gc.py v2/tests/test_chunk_populate.py v2/tests/test_chunk_vectors.py v2/tests/test_deep_fallback_ladder.py -q`
Expected: all PASS.

- [ ] **Step 2: Dev-copy clean run (real Ollama)**

```bash
cp gsa_gateway.db /tmp/dev_embed.db
python3 v2/scripts/embed_chunks.py --db /tmp/dev_embed.db; echo "exit=$?"
```
Expected: `chunked=0 vectors=0 … failed=0 gc_swept=<~200> … DONE; invariant OK`, `exit=0`. (gc_swept removes the pre-existing orphan rows; vectors=0 because dev already fully covered.)

- [ ] **Step 3: Dev-copy convergence proof (induce + recover, real Ollama)** — delete a few chunk vectors to simulate holes, then show a plain re-run heals them without `--force`:

```bash
python3 - <<'PY'
import sqlite3, sqlite_vec
c = sqlite3.connect("/tmp/dev_embed.db"); c.enable_load_extension(True); sqlite_vec.load(c)
ids = [r[0] for r in c.execute("SELECT chunk_id FROM knowledge_chunk_vectors LIMIT 5")]
c.executemany("DELETE FROM knowledge_chunk_vectors WHERE chunk_id=?", [(i,) for i in ids]); c.commit()
print("deleted", len(ids), "chunk vectors")
PY
python3 v2/scripts/embed_chunks.py --db /tmp/dev_embed.db; echo "exit=$?"
```
Expected: the re-run reports `starting_holes=5 vectors=5 failed=0 … DONE; invariant OK`, `exit=0` — **no `--force`**. This is the headline behavior, proven on real data.

- [ ] **Step 4: Present the diff + dev evidence to the owner** for sign-off before merge. (No live write occurs in this plan; the deploy is simply that the next normal embed run is self-healing. No bot restart — embed scripts aren't in the serving path. Coordinate any live embed with the parallel agents.)

---

## Self-Review

**1. Spec coverage:** retry helper (§4.1)→Task 1; orphan-row GC + condition-4-honesty (§4.3)→Task 2; convergent Phase 2 + batch-degrade + per-slot exact-input retry + outage-abort + health-check + report + exit code + corpus_ready rationale (§4.2/§6)→Task 3; embed_all policy share, no double-normalize (§4.4)→Task 4; dev validation + convergence proof (§7/§8)→Task 5. Deferred (M2 truncation, runner sequencing, `populate_item_chunks`) correctly out of scope (spec §2/§9).

**2. Placeholder scan:** none — every code step is complete. Step 2 of Task 4 notes the test may already pass under the existing 2-attempt loop; that's an explicit instruction, not a placeholder.

**3. Type consistency:** `embed_with_retry(call, attempts, backoff)→list|None` used identically in Tasks 3/4. `run_chunk_embed(...)→dict` keys (`chunked/vectors/retried/failed/aborted/starting_holes`) match between the implementation and the tests. `sweep_orphan_chunk_rows`/`count_orphan_chunk_rows` match Task 2. `_prepare` uses `d.doc_prefix + d.truncate_to_tokens(text, d.context_window)` — same as the spec's `embed_input` and the C1 test assertion (`startswith(d.doc_prefix)`).
