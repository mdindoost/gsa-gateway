# Foundation Plan 1 — Chunk Schema + Vector GC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the additive `knowledge_chunks` + filterable `knowledge_chunk_vectors` tables and a reconcile-style vector GC that deletes orphaned vectors (fixes the ~891 orphans leaking in the live DB today), with an invariant test — all with zero change to serving behavior.

**Architecture:** Pure-additive schema (new tables only; nothing existing altered) plus a standalone GC module that sweeps vectors whose parent is missing or `is_active=0`. The GC works for both the current per-item `knowledge_vectors` (fixing the existing bug now) and the new per-chunk `knowledge_chunk_vectors` (used by later plans). A gated runner applies it to the live DB behind `hardened_backup` + `--commit`.

**Tech Stack:** Python 3.11, sqlite3, sqlite-vec (vec0, v0.1.9 — supports partition-key + metadata-column filtered KNN, verified), pytest.

## Global Constraints
- `knowledge_items.is_active=1` means current version; `0` means superseded/departed (soft-delete; the row persists). Orphan = a vector whose `item_id`/`parent_id` has no row OR an `is_active=0` row.
- Never insert `search_text` (generated column). Not touched here.
- vec0 virtual tables cannot be STRICT and cannot have FKs; chunk lifecycle is enforced by GC + the invariant test, NOT FK cascade (the system soft-deletes, so cascade never fires).
- All live-DB writes are gated: `hardened_backup(db_path, label)` first, dry-run default, `--commit` to apply. Signature: `hardened_backup(db_path: str, label: str, keep: int = 10, ...)` in `scripts/_area_tag_migrate.py:35`.
- Embedding dim for the current model (nomic-embed-text) is 768. The chunk-vector table hardcodes `FLOAT[768]` for now; Plan 2 introduces the model descriptor that owns this value.
- No Claude attribution in any commit message.
- Work on branch `feat/durable-retrieval-foundation`.

---

### Task 1: `knowledge_chunks` table (STRICT, additive)

**Files:**
- Modify: `v2/core/database/schema.py` (add DDL constant after `KNOWLEDGE_ITEMS` ~line 79; register in `_TABLE_DDL` ~line 483)
- Test: `v2/tests/test_chunk_schema.py`

**Interfaces:**
- Produces: table `knowledge_chunks(id, parent_id, source_key, ordinal, text, content_hash, model_id, created_at)`; `parent_id REFERENCES knowledge_items(id) ON DELETE CASCADE` (hard-delete backstop only).

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_chunk_schema.py
import sqlite3
from v2.core.database.schema import create_all

def test_knowledge_chunks_table_exists_and_shape(tmp_path):
    db = str(tmp_path / "t.db")
    conn = create_all(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(knowledge_chunks)")}
    assert cols == {"id", "parent_id", "source_key", "ordinal", "text",
                    "content_hash", "model_id", "created_at"}

def test_knowledge_chunks_is_strict(tmp_path):
    db = str(tmp_path / "t.db")
    conn = create_all(db)
    # STRICT tables reject a TEXT value in an INTEGER column.
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES (1,'O','o','custom')")
    conn.execute("INSERT INTO knowledge_items(id,org_id,type,content) VALUES (1,1,'policy','x')")
    try:
        conn.execute("INSERT INTO knowledge_chunks(parent_id,source_key,ordinal,text,content_hash,model_id) "
                     "VALUES ('not-an-int','s',0,'t','h','m')")
        assert False, "STRICT table should reject TEXT in INTEGER column"
    except sqlite3.IntegrityError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest v2/tests/test_chunk_schema.py -v`
Expected: FAIL (`no such table: knowledge_chunks`).

- [ ] **Step 3: Add the DDL and register it**

In `v2/core/database/schema.py`, after the `KNOWLEDGE_ITEMS` constant block:

```python
KNOWLEDGE_CHUNKS = """
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id           INTEGER PRIMARY KEY,
    parent_id    INTEGER NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
    source_key   TEXT    NOT NULL,              -- stable per-parent key for invalidation
    ordinal      INTEGER NOT NULL,              -- chunk position within the parent (0-based)
    text         TEXT    NOT NULL,              -- verbatim slice of the parent content
    content_hash TEXT    NOT NULL,              -- hash of (chunk text + model_id) for change-detect
    model_id     TEXT    NOT NULL,              -- embedding-model descriptor id that chunked this
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(parent_id, ordinal)
) STRICT;
"""
```

In `_TABLE_DDL`, add `KNOWLEDGE_CHUNKS` immediately after `KNOWLEDGE_ITEMS,` (so the FK target exists first).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest v2/tests/test_chunk_schema.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add v2/core/database/schema.py v2/tests/test_chunk_schema.py
git commit -m "feat(schema): add additive knowledge_chunks table (parent-document chunks)"
```

---

### Task 2: `knowledge_chunk_vectors` vec0 table with filtered KNN

**Files:**
- Modify: `v2/core/database/schema.py` (add DDL after `KNOWLEDGE_VECTORS` ~line 210; register in `_TABLE_DDL`)
- Test: `v2/tests/test_chunk_vectors.py`

**Interfaces:**
- Produces: vec0 table `knowledge_chunk_vectors(chunk_id PK, embedding FLOAT[768], org_id partition key, type metadata, +parent_id aux)` supporting `WHERE org_id=? AND embedding MATCH ?` filtered KNN.

- [ ] **Step 1: Write the failing test** (this is also the proof that filtered KNN works on the installed sqlite-vec)

```python
# v2/tests/test_chunk_vectors.py
from v2.core.database.schema import create_all
import sqlite_vec, struct

def _vec(xs):
    return struct.pack(f"{len(xs)}f", *xs)

def test_chunk_vectors_filtered_knn(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    v = [0.0] * 768
    a = v.copy(); a[0] = 1.0           # org 10
    b = v.copy(); b[0] = 0.9           # org 20 (closest to query but wrong org)
    conn.execute("INSERT INTO knowledge_chunk_vectors(chunk_id,embedding,org_id,type,parent_id) "
                 "VALUES (1,?,10,'policy',100)", (_vec(a),))
    conn.execute("INSERT INTO knowledge_chunk_vectors(chunk_id,embedding,org_id,type,parent_id) "
                 "VALUES (2,?,20,'policy',200)", (_vec(b),))
    q = v.copy(); q[0] = 0.95
    # Filtered KNN: restrict to org 10 -> must return chunk 1, never chunk 2.
    rows = conn.execute(
        "SELECT chunk_id, parent_id FROM knowledge_chunk_vectors "
        "WHERE org_id = 10 AND embedding MATCH ? ORDER BY distance LIMIT 5", (_vec(q),)
    ).fetchall()
    assert [r[0] for r in rows] == [1]
    assert rows[0][1] == 200 - 100      # parent_id aux retrievable (= 100)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest v2/tests/test_chunk_vectors.py -v`
Expected: FAIL (`no such table: knowledge_chunk_vectors`).

- [ ] **Step 3: Add the DDL and register it**

After the `KNOWLEDGE_VECTORS` constant:

```python
KNOWLEDGE_CHUNK_VECTORS = """
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunk_vectors USING vec0(
    chunk_id  INTEGER PRIMARY KEY,             -- = knowledge_chunks.id
    embedding FLOAT[768],                       -- nomic-embed-text (dim owned by descriptor in Plan 2)
    org_id    INTEGER partition key,            -- in-engine filter for org-scoped queries (ARCH R3)
    type      TEXT,                             -- metadata column (filterable)
    +parent_id INTEGER                          -- auxiliary: collapse chunk -> parent item
);
"""
```

Register `KNOWLEDGE_CHUNK_VECTORS` in `_TABLE_DDL` right after `KNOWLEDGE_VECTORS,`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest v2/tests/test_chunk_vectors.py -v`
Expected: PASS. (If it errors on the vec0 syntax, the installed sqlite-vec is < the metadata release — STOP and report; the spec's filter-pushdown depends on this.)

- [ ] **Step 5: Commit**

```bash
git add v2/core/database/schema.py v2/tests/test_chunk_vectors.py
git commit -m "feat(schema): add knowledge_chunk_vectors vec0 table with org_id/type filter + parent_id aux"
```

---

### Task 3: Vector GC — orphan detection + sweep (fixes the current 891-orphan bug)

**Files:**
- Create: `v2/core/database/vector_gc.py`
- Test: `v2/tests/test_vector_gc.py`

**Interfaces:**
- Produces:
  - `count_orphan_item_vectors(conn) -> int`
  - `sweep_orphan_item_vectors(conn) -> int` (returns # deleted; caller commits)
  - `count_orphan_chunk_vectors(conn) -> int`
  - `sweep_orphan_chunk_vectors(conn) -> int`
  - `assert_no_orphans(conn) -> None` (raises `AssertionError` if either count > 0 — the invariant)

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_vector_gc.py
import struct
from v2.core.database.schema import create_all
from v2.core.database import vector_gc

def _vec(n=768): return struct.pack(f"{n}f", *([0.0] * n))

def _seed(conn):
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES (1,'O','o','custom')")
    # active item 1, superseded item 2 (is_active=0); item 3 never existed
    conn.execute("INSERT INTO knowledge_items(id,org_id,type,content,is_active) VALUES (1,1,'policy','a',1)")
    conn.execute("INSERT INTO knowledge_items(id,org_id,type,content,is_active) VALUES (2,1,'policy','b',0)")
    for iid in (1, 2, 3):
        conn.execute("INSERT INTO knowledge_vectors(item_id,embedding) VALUES (?,?)", (iid, _vec()))

def test_count_and_sweep_orphan_item_vectors(tmp_path):
    conn = create_all(str(tmp_path / "t.db")); _seed(conn)
    # item 2 (is_active=0) and item 3 (missing) are orphans; item 1 is live.
    assert vector_gc.count_orphan_item_vectors(conn) == 2
    deleted = vector_gc.sweep_orphan_item_vectors(conn)
    assert deleted == 2
    assert vector_gc.count_orphan_item_vectors(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM knowledge_vectors").fetchone()[0] == 1

def test_sweep_orphan_chunk_vectors(tmp_path):
    conn = create_all(str(tmp_path / "t.db")); _seed(conn)
    conn.execute("INSERT INTO knowledge_chunks(id,parent_id,source_key,ordinal,text,content_hash,model_id) "
                 "VALUES (10,1,'s',0,'t','h','m')")        # parent active -> keep
    conn.execute("INSERT INTO knowledge_chunk_vectors(chunk_id,embedding,org_id,type,parent_id) "
                 "VALUES (10,?,1,'policy',1)", (_vec(),))
    conn.execute("INSERT INTO knowledge_chunk_vectors(chunk_id,embedding,org_id,type,parent_id) "
                 "VALUES (11,?,1,'policy',2)", (_vec(),))  # parent item 2 is_active=0 -> orphan
    conn.execute("INSERT INTO knowledge_chunk_vectors(chunk_id,embedding,org_id,type,parent_id) "
                 "VALUES (12,?,1,'policy',999)", (_vec(),))# parent missing -> orphan
    assert vector_gc.count_orphan_chunk_vectors(conn) == 2
    assert vector_gc.sweep_orphan_chunk_vectors(conn) == 2
    assert vector_gc.count_orphan_chunk_vectors(conn) == 0

def test_assert_no_orphans_raises_then_passes(tmp_path):
    conn = create_all(str(tmp_path / "t.db")); _seed(conn)
    try:
        vector_gc.assert_no_orphans(conn); assert False
    except AssertionError:
        pass
    vector_gc.sweep_orphan_item_vectors(conn)
    vector_gc.assert_no_orphans(conn)   # no raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest v2/tests/test_vector_gc.py -v`
Expected: FAIL (`No module named 'v2.core.database.vector_gc'`).

- [ ] **Step 3: Write minimal implementation**

```python
# v2/core/database/vector_gc.py
"""Vector garbage collection: delete vectors whose parent item is gone or superseded.

The system SOFT-deletes knowledge_items (is_active=0 + a new row), so FK cascade
never fires and vectors orphan by inactivity. This is the GC net (and the invariant
that enforces it). Works for both the per-item knowledge_vectors and the per-chunk
knowledge_chunk_vectors. Callers own the transaction (these do not commit).
"""
from __future__ import annotations
import sqlite3

_ITEM_ORPHANS = """
    SELECT v.item_id FROM knowledge_vectors v
    LEFT JOIN knowledge_items i ON i.id = v.item_id AND i.is_active = 1
    WHERE i.id IS NULL
"""
_CHUNK_ORPHANS = """
    SELECT cv.chunk_id FROM knowledge_chunk_vectors cv
    LEFT JOIN knowledge_chunks c ON c.id = cv.chunk_id
    LEFT JOIN knowledge_items i ON i.id = c.parent_id AND i.is_active = 1
    WHERE i.id IS NULL
"""

def count_orphan_item_vectors(conn: sqlite3.Connection) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM ({_ITEM_ORPHANS})").fetchone()[0]

def sweep_orphan_item_vectors(conn: sqlite3.Connection) -> int:
    ids = [r[0] for r in conn.execute(_ITEM_ORPHANS)]
    conn.executemany("DELETE FROM knowledge_vectors WHERE item_id = ?", [(i,) for i in ids])
    return len(ids)

def count_orphan_chunk_vectors(conn: sqlite3.Connection) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM ({_CHUNK_ORPHANS})").fetchone()[0]

def sweep_orphan_chunk_vectors(conn: sqlite3.Connection) -> int:
    ids = [r[0] for r in conn.execute(_CHUNK_ORPHANS)]
    conn.executemany("DELETE FROM knowledge_chunk_vectors WHERE chunk_id = ?", [(i,) for i in ids])
    return len(ids)

def assert_no_orphans(conn: sqlite3.Connection) -> None:
    item = count_orphan_item_vectors(conn)
    chunk = count_orphan_chunk_vectors(conn)
    assert item == 0 and chunk == 0, f"orphan vectors present: item={item} chunk={chunk}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest v2/tests/test_vector_gc.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add v2/core/database/vector_gc.py v2/tests/test_vector_gc.py
git commit -m "feat(db): vector GC for soft-deleted parents + no-orphans invariant"
```

---

### Task 4: Gated runner `scripts/gc_vectors.py` (dry-run default; --commit applies)

**Files:**
- Create: `scripts/gc_vectors.py`
- Test: `v2/tests/test_gc_vectors_runner.py`

**Interfaces:**
- Consumes: `vector_gc.*`, `schema.get_connection`, `_area_tag_migrate.hardened_backup`.
- Produces: a CLI that prints orphan counts; with `--commit` takes a `hardened_backup` then sweeps and verifies `assert_no_orphans`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_gc_vectors_runner.py
import struct, subprocess, sys
from v2.core.database.schema import create_all

def _vec(n=768): return struct.pack(f"{n}f", *([0.0] * n))

def test_runner_dryrun_reports_but_keeps(tmp_path):
    db = str(tmp_path / "t.db")
    conn = create_all(db)
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES (1,'O','o','custom')")
    conn.execute("INSERT INTO knowledge_vectors(item_id,embedding) VALUES (5,?)", (_vec(),))  # orphan
    conn.commit(); conn.close()
    out = subprocess.run([sys.executable, "scripts/gc_vectors.py", "--db", db],
                         capture_output=True, text=True)
    assert "orphan" in out.stdout.lower()
    conn = create_all(db)
    assert conn.execute("SELECT COUNT(*) FROM knowledge_vectors").fetchone()[0] == 1  # untouched
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest v2/tests/test_gc_vectors_runner.py -v`
Expected: FAIL (`can't open file 'scripts/gc_vectors.py'`).

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/gc_vectors.py
"""Sweep orphaned vectors (item + chunk). Dry-run default; --commit applies after a hardened backup."""
import argparse, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path: sys.path.insert(0, str(REPO))
from v2.core.database.schema import get_connection           # noqa: E402
from v2.core.database import vector_gc                        # noqa: E402
from scripts._area_tag_migrate import hardened_backup         # noqa: E402

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    conn = get_connection(args.db)
    item = vector_gc.count_orphan_item_vectors(conn)
    chunk = vector_gc.count_orphan_chunk_vectors(conn)
    print(f"orphan vectors: item={item} chunk={chunk}")
    if not args.commit:
        print("dry-run — pass --commit to delete (a hardened backup is taken first)."); return
    hardened_backup(args.db, "gc-vectors")
    d1 = vector_gc.sweep_orphan_item_vectors(conn)
    d2 = vector_gc.sweep_orphan_chunk_vectors(conn)
    conn.commit()
    vector_gc.assert_no_orphans(conn)
    print(f"deleted item={d1} chunk={d2}; invariant OK (0 orphans).")

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest v2/tests/test_gc_vectors_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/gc_vectors.py v2/tests/test_gc_vectors_runner.py
git commit -m "feat(scripts): gated gc_vectors runner (dry-run default, hardened backup on --commit)"
```

---

### Task 5: Verify against a copy of the live DB (proof, no live write)

**Files:** none (verification task).

- [ ] **Step 1: Copy the live DB and run create_all + dry-run GC**

```bash
cp gsa_gateway.db /tmp/gc_check.db
python -c "from v2.core.database.schema import create_all; create_all('/tmp/gc_check.db')"  # additive tables
python scripts/gc_vectors.py --db /tmp/gc_check.db
```
Expected: prints `orphan vectors: item=<~891> chunk=0` (chunk=0 — no chunks yet). Confirms the additive tables apply cleanly to the real schema and the orphan count matches the spec's ~891.

- [ ] **Step 2: Apply on the copy and confirm the invariant**

```bash
python scripts/gc_vectors.py --db /tmp/gc_check.db --commit
python scripts/gc_vectors.py --db /tmp/gc_check.db
```
Expected: first run deletes ~891 and prints "invariant OK"; second run prints `item=0 chunk=0`.

- [ ] **Step 3: Run the full new test suite**

Run: `python -m pytest v2/tests/test_chunk_schema.py v2/tests/test_chunk_vectors.py v2/tests/test_vector_gc.py v2/tests/test_gc_vectors_runner.py -v`
Expected: all PASS.

- [ ] **Step 4: CHECKPOINT — do NOT run `--commit` on the live `gsa_gateway.db`.**

Report the copy results to the owner. The live GC run (which deletes the ~891 real orphans behind a hardened backup) is a production write — it waits for owner go, batched with the Plan-4 cutover decision or approved standalone.

---

## Self-Review
- **Spec coverage:** §4 chunk tables (Task 1, 2 incl. org_id/type/parent_id columns), §6 invalidation GC + invariant (Task 3), the "ship GC vs current DB first / fixes 891" of §14 plan 1 (Task 4, 5). The "wired into EVERY writer" wiring is Plan 2 (it needs the chunker); this plan delivers the GC net + invariant standalone.
- **Placeholder scan:** none — every step has real code/commands.
- **Type consistency:** `vector_gc` function names match between Task 3 definition and Task 4 usage; table/column names match the DDL in Tasks 1–2.
- **No behavior change:** new tables are unused by the retriever until Plan 3; GC only deletes already-dead vectors.
