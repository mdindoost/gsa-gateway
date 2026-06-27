"""TDD tests for assert_chunk_invariant + corpus_build_ready.

Tests are written BEFORE the implementation. They exercise:
- well-formed corpus passes
- active served item without chunks raises
- stale model_id raises (both full-stale and partial-reembed scenarios)
- chunk with no vector (active parent) raises (condition 2)
- descriptor dim mismatch raises (condition 5)
- item of excluded type (publication) without chunks does NOT raise
- corpus_build_ready returns True/False accordingly
"""
import dataclasses
import struct

import pytest

from v2.core.database.schema import create_all
from v2.core.retrieval.model_descriptor import active_descriptor
from v2.core.database.vector_gc import assert_chunk_invariant, corpus_build_ready

D = active_descriptor()


def _v():
    """Return a packed float32 embedding of `D.dim` zeros."""
    return struct.pack(f"{D.dim}f", *([0.0] * D.dim))


def _seed_org(conn):
    """Insert a single org row so FK constraints on knowledge_items pass."""
    conn.execute(
        "INSERT OR IGNORE INTO organizations(id,name,slug,type) VALUES (1,'A','a','office')"
    )


def _served_item(conn, iid, model_id=None):
    """Insert a fully-wired served item: item + chunk + chunk-vector."""
    if model_id is None:
        model_id = D.id
    _seed_org(conn)
    conn.execute(
        "INSERT INTO knowledge_items(id,org_id,type,content,is_active) "
        "VALUES (?,1,'policy','x',1)",
        (iid,),
    )
    cur = conn.execute(
        "INSERT INTO knowledge_chunks(parent_id,source_key,ordinal,text,content_hash,model_id) "
        "VALUES (?,?,0,'x','h',?)",
        (iid, f"item:{iid}", model_id),
    )
    conn.execute(
        "INSERT INTO knowledge_chunk_vectors(chunk_id,embedding,org_id,type,parent_id) "
        "VALUES (?,?,1,'policy',?)",
        (cur.lastrowid, _v(), iid),
    )


# ── Cases from the brief ─────────────────────────────────────────────────────

def test_invariant_passes_for_well_formed(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    _served_item(conn, 100)
    assert_chunk_invariant(conn, D)   # must NOT raise


def test_invariant_fails_active_item_without_chunks(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    _seed_org(conn)
    conn.execute(
        "INSERT INTO knowledge_items(id,org_id,type,content,is_active) "
        "VALUES (100,1,'policy','x',1)"
    )
    with pytest.raises(AssertionError):
        assert_chunk_invariant(conn, D)


def test_invariant_fails_stale_model_id(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    _served_item(conn, 100, model_id="old-model@v0")
    with pytest.raises(AssertionError):
        assert_chunk_invariant(conn, D)


# ── Extra: excluded-type (publication) without chunks must NOT raise ──────────

def test_invariant_passes_excluded_type_without_chunks(tmp_path):
    """A publication item (excluded from served corpus) without chunks is OK."""
    conn = create_all(str(tmp_path / "t.db"))
    _seed_org(conn)
    conn.execute(
        "INSERT INTO knowledge_items(id,org_id,type,content,is_active) "
        "VALUES (200,1,'publication','pub text',1)"
    )
    # No chunks inserted — should NOT raise because 'publication' is excluded.
    assert_chunk_invariant(conn, D)


# ── Condition 4 (stale model_id) — INDEPENDENT partial-reembed failure ───────

def test_invariant_fails_partial_reembed_stale_chunk(tmp_path):
    """Condition 4 fires even when condition 1 passes (partial re-embed scenario).

    An item has BOTH a current-model chunk+vector (condition 1 satisfied) AND a
    residual stale-model chunk+vector left over from a previous embed pass.
    Condition 4 must fire; the AssertionError message must mention 'stale'.
    """
    conn = create_all(str(tmp_path / "t.db"))
    # Current-model chunk + vector → condition 1 satisfied.
    _served_item(conn, 100)
    # Residual stale-model chunk + vector → condition 4 fires.
    cur = conn.execute(
        "INSERT INTO knowledge_chunks(parent_id, source_key, ordinal, text, content_hash, model_id) "
        "VALUES (100, 'item:100', 1, 'y', 'h2', 'old-model@v0')"
    )
    conn.execute(
        "INSERT INTO knowledge_chunk_vectors(chunk_id,embedding,org_id,type,parent_id) "
        "VALUES (?,?,1,'policy',100)",
        (cur.lastrowid, _v()),
    )
    with pytest.raises(AssertionError, match="stale"):
        assert_chunk_invariant(conn, D)


# ── Condition 2 — chunk with no vector (active parent) raises ─────────────────

def test_invariant_fails_chunk_without_vector(tmp_path):
    """Condition 2: an active-parent chunk with no knowledge_chunk_vectors row raises."""
    conn = create_all(str(tmp_path / "t.db"))
    _seed_org(conn)
    conn.execute(
        "INSERT INTO knowledge_items(id,org_id,type,content,is_active) "
        "VALUES (100,1,'policy','x',1)"
    )
    # Insert chunk with current model_id (condition 1 would pass if we had a vector).
    conn.execute(
        "INSERT INTO knowledge_chunks(parent_id, source_key, ordinal, text, content_hash, model_id) "
        "VALUES (100, 'item:100', 0, 'x', 'h', ?)",
        (D.id,),
    )
    # No knowledge_chunk_vectors row → condition 2 fires.
    with pytest.raises(AssertionError):
        assert_chunk_invariant(conn, D)


# ── Condition 5 — descriptor dim mismatch raises ──────────────────────────────

def test_invariant_fails_dim_mismatch(tmp_path):
    """Condition 5: descriptor.dim != schema dim raises AssertionError."""
    conn = create_all(str(tmp_path / "t.db"))
    _served_item(conn, 100)
    # Schema is built for D.dim (768); pass a descriptor with a different dim.
    wrong_dim = dataclasses.replace(D, dim=512)
    with pytest.raises(AssertionError):
        assert_chunk_invariant(conn, wrong_dim)


# ── corpus_build_ready wraps assert_chunk_invariant ──────────────────────────

def test_corpus_build_ready_true_for_well_formed(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    _served_item(conn, 100)
    assert corpus_build_ready(conn, D) is True


def test_corpus_build_ready_false_when_invariant_fails(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    _seed_org(conn)
    conn.execute(
        "INSERT INTO knowledge_items(id,org_id,type,content,is_active) "
        "VALUES (100,1,'policy','x',1)"
    )
    assert corpus_build_ready(conn, D) is False
