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


# ── Task 2: orphan chunk-ROW GC ──────────────────────────────────────────────

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


# ── Task 3: run_chunk_embed ───────────────────────────────────────────────────

import sqlite_vec
import pytest


def _norm_bytes():
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
    # First run: fake drops item-2's chunk(s) in both paths -> a residual hole.
    res = run_chunk_embed(conn, _D, FakeEmb(fail_texts=["bravo content two"]), batch=8, backoff=0)
    assert res["failed"] >= 1
    with pytest.raises(AssertionError):
        assert_chunk_invariant(conn, _D)
    # Re-run with a healthy fake, NO --force -> backfills the hole, invariant passes.
    res2 = run_chunk_embed(conn, _D, FakeEmb(), batch=8, backoff=0)
    assert res2["failed"] == 0
    assert_chunk_invariant(conn, _D)  # no raise


def test_run_chunk_embed_batch_exception_degrades_not_crash():
    from v2.scripts.embed_chunks import run_chunk_embed
    conn = create_all(":memory:")
    _seed_item(conn, 1, "gamma content")
    res = run_chunk_embed(conn, _D, FakeEmb(raise_batch=True), batch=8, backoff=0)
    # batch raised once -> degraded to per-slot retry -> chunk(s) embedded, no traceback, failed=0
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


# ── Task 4: embed_all uses embed_with_retry ───────────────────────────────────

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
