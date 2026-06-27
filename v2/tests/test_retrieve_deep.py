"""Tests for retrieve(semantic_mode=...) and retrieve_deep() — Task 2.

RED phase: these tests must fail before implementation.
"""
import struct
from v2.core.database.schema import create_all
from v2.core.retrieval.retriever import V2Retriever


def _v(idx, val=1.0):
    x = [0.0] * 768
    x[idx] = val
    return struct.pack("768f", *x)


def _setup(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES (1,'A','a','office')")
    conn.execute(
        "INSERT INTO knowledge_items(id,org_id,type,content) VALUES "
        "(100,1,'policy','deep page'),(200,1,'policy','other')"
    )
    conn.execute(
        "INSERT INTO knowledge_chunk_vectors(chunk_id,embedding,org_id,type,parent_id) "
        "VALUES (1,?,1,'policy',100),(2,?,1,'policy',200)",
        (_v(0, 1.0), _v(1, 1.0)),
    )
    return conn


def test_retrieve_deep_uses_chunk_leg_even_when_use_chunks_off(tmp_path):
    conn = _setup(tmp_path)
    r = V2Retriever(conn, embedder=object())
    assert r.use_chunks is False                      # default off
    out = r.retrieve_deep("q", query_vec=list(struct.unpack("768f", _v(0, 1.0))), limit=5)
    assert out and out[0].item_id == 100              # chunk-KNN found the matching parent


def test_semantic_mode_whole_doc_matches_default(tmp_path):
    conn = _setup(tmp_path)
    # add an item vector so whole_doc has something to find
    conn.execute(
        "INSERT INTO knowledge_vectors(item_id,embedding) VALUES (200,?)", (_v(1, 1.0),)
    )
    r = V2Retriever(conn, embedder=object())
    qv = list(struct.unpack("768f", _v(1, 1.0)))
    a = r.retrieve("q", query_vec=qv, limit=5)
    b = r.retrieve("q", query_vec=qv, limit=5, semantic_mode="whole_doc")
    assert [c.item_id for c in a] == [c.item_id for c in b]   # None == explicit whole_doc
