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
