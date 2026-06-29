# v2/tests/test_deep_fallback_ladder.py
# Pure unit test of the adopt-if-better decision, extracted as a helper.
import struct

from bot.core.message_handler import _deep_adopt   # to be added in Step 4
from v2.core.database.schema import create_all, get_connection
from v2.core.retrieval.model_descriptor import active_descriptor
from v2.integration.retriever_shim import V2RetrieverShim

_D = active_descriptor()


def test_adopt_when_strictly_better_and_over_threshold():
    assert _deep_adopt(current_rel=0.10, rescue_rel=0.40, threshold=0.15) is True


def test_reject_when_not_better():
    assert _deep_adopt(current_rel=0.50, rescue_rel=0.40, threshold=0.15) is False


def test_reject_when_below_threshold():
    assert _deep_adopt(current_rel=0.05, rescue_rel=0.12, threshold=0.15) is False


def test_adopt_when_current_is_none_but_over_threshold():
    # relevance None => not a miss for the normal path, but if we got here (no chunks) adopt if >=T
    assert _deep_adopt(current_rel=None, rescue_rel=0.40, threshold=0.15) is True


def test_reject_when_rescue_rel_none():
    assert _deep_adopt(current_rel=0.10, rescue_rel=None, threshold=0.15) is False


def test_adopt_when_exactly_at_threshold():
    assert _deep_adopt(current_rel=0.10, rescue_rel=0.15, threshold=0.15) is True


# ── A1: deep-fallback rescue is gated on corpus_build_ready ───────────────────
# The shim is the seam the hot path uses to reach a connection; corpus_ready()
# answers "is the chunk corpus built+coherent for the active model?" (cached
# per-process). The handler skips the deep branch when it is False, so flipping
# RETRIEVAL_DEEP_FALLBACK on a DB whose chunks aren't built is a safe no-op.

def _seed_served_item(conn):
    """A servable item with NO chunks — the realistic 'chunks not built yet' state."""
    conn.execute(
        "INSERT OR IGNORE INTO organizations(id,name,slug,type) VALUES (1,'A','a','office')")
    conn.execute(
        "INSERT OR IGNORE INTO knowledge_items(id,org_id,type,content,is_active) "
        "VALUES (100,1,'policy','x',1)")
    conn.commit()


def _seed_served_chunk(conn):
    """A fully-wired served item: item + chunk + chunk-vector → invariant holds."""
    _seed_served_item(conn)
    cur = conn.execute(
        "INSERT INTO knowledge_chunks(parent_id,source_key,ordinal,text,content_hash,model_id) "
        "VALUES (100,'item:100',0,'x','h',?)", (_D.id,))
    conn.execute(
        "INSERT INTO knowledge_chunk_vectors(chunk_id,embedding,org_id,type,parent_id) "
        "VALUES (?,?,1,'policy',100)",
        (cur.lastrowid, struct.pack(f"{_D.dim}f", *([0.0] * _D.dim))))
    conn.commit()


def test_corpus_ready_false_when_chunks_not_built(tmp_path):
    db = str(tmp_path / "t.db")
    conn = create_all(db)
    _seed_served_item(conn)                  # servable content, but chunks not built
    conn.close()
    shim = V2RetrieverShim(db_path=db, embedder=None)
    assert shim.corpus_ready() is False     # uncovered served item → deep branch skipped


def test_corpus_ready_true_when_chunks_built(tmp_path):
    db = str(tmp_path / "t.db")
    conn = create_all(db)
    _seed_served_chunk(conn)
    conn.close()
    shim = V2RetrieverShim(db_path=db, embedder=None)
    assert shim.corpus_ready() is True       # built corpus → existing behavior


def test_corpus_ready_cached_per_process(tmp_path):
    db = str(tmp_path / "t.db")
    conn = create_all(db)
    _seed_served_item(conn)                  # servable content, chunks not built
    shim = V2RetrieverShim(db_path=db, embedder=None)
    assert shim.corpus_ready() is False      # not ready yet → result cached
    _seed_served_chunk(conn)                 # build it AFTER the first check
    conn.close()
    # Cheap-but-not-free check is cached per-process (flags only flip at restart).
    assert shim.corpus_ready() is False
