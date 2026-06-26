import struct

from v2.core.database.schema import create_all
from v2.core.database import vector_gc


def _vec(n=768):
    return struct.pack(f"{n}f", *([0.0] * n))


def _seed(conn):
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES (1,'O','o','custom')")
    conn.execute("INSERT INTO knowledge_items(id,org_id,type,content,is_active) VALUES (1,1,'policy','a',1)")
    conn.execute("INSERT INTO knowledge_items(id,org_id,type,content,is_active) VALUES (2,1,'policy','b',0)")
    for iid in (1, 2, 3):
        conn.execute("INSERT INTO knowledge_vectors(item_id,embedding) VALUES (?,?)", (iid, _vec()))


def test_count_and_sweep_orphan_item_vectors(tmp_path):
    conn = create_all(str(tmp_path / "t.db")); _seed(conn)
    assert vector_gc.count_orphan_item_vectors(conn) == 2
    assert vector_gc.sweep_orphan_item_vectors(conn) == 2
    assert vector_gc.count_orphan_item_vectors(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM knowledge_vectors").fetchone()[0] == 1


def test_sweep_orphan_chunk_vectors(tmp_path):
    conn = create_all(str(tmp_path / "t.db")); _seed(conn)
    conn.execute("INSERT INTO knowledge_chunks(id,parent_id,source_key,ordinal,text,content_hash,model_id) "
                 "VALUES (10,1,'s',0,'t','h','m')")
    conn.execute("INSERT INTO knowledge_chunk_vectors(chunk_id,embedding,org_id,type,parent_id) "
                 "VALUES (10,?,1,'policy',1)", (_vec(),))
    conn.execute("INSERT INTO knowledge_chunk_vectors(chunk_id,embedding,org_id,type,parent_id) "
                 "VALUES (11,?,1,'policy',2)", (_vec(),))
    conn.execute("INSERT INTO knowledge_chunk_vectors(chunk_id,embedding,org_id,type,parent_id) "
                 "VALUES (12,?,1,'policy',999)", (_vec(),))
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
    vector_gc.assert_no_orphans(conn)
