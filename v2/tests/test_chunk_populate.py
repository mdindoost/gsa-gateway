from v2.core.database.schema import create_all
from v2.core.database import vector_gc
from v2.core.retrieval.chunk_populate import populate_item_chunks, drop_item_chunks
from v2.core.retrieval.model_descriptor import active_descriptor

D = active_descriptor()
LONG = ("The Office of the Registrar processes registration holds and transcript requests. "
        "Students resolve advising holds before registration each term. ") * 120


def _fake_embed(_text):
    return [0.1] * 768          # constant non-zero vector (normalizes fine)


def _seed(conn, content, is_active=1):
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES (1,'O','o','custom')")
    conn.execute("INSERT INTO knowledge_items(id,org_id,type,content,is_active) VALUES (1,1,'policy',?,?)",
                 (content, is_active))


def test_populate_writes_matching_chunks_and_vectors(tmp_path):
    conn = create_all(str(tmp_path / "t.db")); _seed(conn, LONG)
    written = populate_item_chunks(conn, 1, _fake_embed, D)
    n_chunks = conn.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE parent_id=1").fetchone()[0]
    n_vecs = conn.execute("SELECT COUNT(*) FROM knowledge_chunk_vectors WHERE parent_id=1").fetchone()[0]
    assert written == n_chunks == n_vecs > 1
    ordinals = [r[0] for r in conn.execute("SELECT ordinal FROM knowledge_chunks WHERE parent_id=1 ORDER BY ordinal")]
    assert ordinals == list(range(n_chunks))                # 0..k-1, no gaps
    # metadata column carried onto the vector row
    assert conn.execute("SELECT DISTINCT type FROM knowledge_chunk_vectors WHERE parent_id=1").fetchone()[0] == "policy"
    vector_gc.assert_no_orphans(conn)                       # everything has an active parent


def test_repopulate_is_idempotent(tmp_path):
    conn = create_all(str(tmp_path / "t.db")); _seed(conn, LONG)
    first = populate_item_chunks(conn, 1, _fake_embed, D)
    second = populate_item_chunks(conn, 1, _fake_embed, D)
    assert first == second
    assert conn.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE parent_id=1").fetchone()[0] == second


def test_superseded_item_yields_no_chunks(tmp_path):
    conn = create_all(str(tmp_path / "t.db")); _seed(conn, LONG, is_active=0)
    assert populate_item_chunks(conn, 1, _fake_embed, D) == 0
    assert conn.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE parent_id=1").fetchone()[0] == 0


def test_drop_item_chunks_clears_both_tables(tmp_path):
    conn = create_all(str(tmp_path / "t.db")); _seed(conn, LONG)
    populate_item_chunks(conn, 1, _fake_embed, D)
    dropped = drop_item_chunks(conn, 1)
    assert dropped > 1
    assert conn.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE parent_id=1").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM knowledge_chunk_vectors WHERE parent_id=1").fetchone()[0] == 0
