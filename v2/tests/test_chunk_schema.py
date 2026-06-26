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
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES (1,'O','o','custom')")
    conn.execute("INSERT INTO knowledge_items(id,org_id,type,content) VALUES (1,1,'policy','x')")
    try:
        conn.execute("INSERT INTO knowledge_chunks(parent_id,source_key,ordinal,text,content_hash,model_id) "
                     "VALUES ('not-an-int','s',0,'t','h','m')")
        assert False, "STRICT table should reject TEXT in INTEGER column"
    except sqlite3.IntegrityError:
        pass
