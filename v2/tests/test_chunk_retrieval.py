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
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES (2,'B','b','office')")
    for iid, org in ((100, 1), (200, 2), (300, 1)):
        conn.execute("INSERT INTO knowledge_items(id,org_id,type,content) VALUES (?,?,'policy','x')", (iid, org))
    # chunk vectors: parent 100 has a near (cid1) + far (cid2) chunk; 200 near-ish; 300 far
    rows = [(1, _v(0, 1.0), 1, 100), (2, _v(1, 1.0), 1, 100),
            (3, _v(0, 0.8), 2, 200), (4, _v(2, 1.0), 1, 300)]
    for cid, emb, org, pid in rows:
        conn.execute("INSERT INTO knowledge_chunk_vectors(chunk_id,embedding,org_id,type,parent_id) "
                     "VALUES (?,?,?,'policy',?)", (cid, emb, org, pid))
    return conn


def test_collapse_to_best_child_and_order(tmp_path):
    conn = _setup(tmp_path)
    r = V2Retriever(conn, embedder=object())
    q = struct.unpack("768f", _v(0, 1.0))
    out = r._semantic_chunks(list(q), fetch=10, allowed=None, org_ids=None)
    parents = [t[0] for t in out]
    assert parents == [100, 200, 300]            # by best-child distance
    assert parents.count(100) == 1               # collapsed (had 2 chunks)


def test_org_partition_pushdown(tmp_path):
    conn = _setup(tmp_path)
    r = V2Retriever(conn, embedder=object())
    q = list(struct.unpack("768f", _v(0, 1.0)))
    out = r._semantic_chunks(q, fetch=10, allowed=None, org_ids=[1])
    assert {t[0] for t in out} == {100, 300}      # org 2 (parent 200) excluded in-engine


def test_allowed_post_filter(tmp_path):
    conn = _setup(tmp_path)
    r = V2Retriever(conn, embedder=object())
    q = list(struct.unpack("768f", _v(0, 1.0)))
    out = r._semantic_chunks(q, fetch=10, allowed={200}, org_ids=None)
    assert [t[0] for t in out] == [200]


def test_flag_default_off_and_env_on(tmp_path, monkeypatch):
    conn = _setup(tmp_path)
    monkeypatch.delenv("RETRIEVAL_CHUNKS", raising=False)
    assert V2Retriever(conn, embedder=object()).use_chunks is False
    monkeypatch.setenv("RETRIEVAL_CHUNKS", "1")
    assert V2Retriever(conn, embedder=object()).use_chunks is True
