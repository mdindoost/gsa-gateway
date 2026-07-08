import os
import tempfile

from v2.core.database.schema import create_knowledge_schema


def test_entity_mentions_in_knowledge_schema():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "k.db")
    conn = create_knowledge_schema(p)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(entity_mentions)")}
    assert {"stable_key", "node_key", "item_id", "node_id", "match_basis",
            "confidence", "created_by", "created_at"} <= cols
    idx = {r[1] for r in conn.execute("PRAGMA index_list(entity_mentions)")}
    assert any("em_node" in i for i in idx)
    # created_at + created_by defaults present (insert without them succeeds)
    conn.execute("INSERT INTO entity_mentions(stable_key,node_key,item_id,node_id,match_basis) "
                 "VALUES('id:64','k',64,1,'title')")
    conn.commit()
    row = conn.execute("SELECT created_at,created_by FROM entity_mentions").fetchone()
    assert row[0] and row[1] == "entity_mentions_tagger"
