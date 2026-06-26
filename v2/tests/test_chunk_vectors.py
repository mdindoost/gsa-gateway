import struct

from v2.core.database.schema import create_all


def _vec(xs):
    return struct.pack(f"{len(xs)}f", *xs)


def test_chunk_vectors_filtered_knn(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    v = [0.0] * 768
    a = v.copy(); a[0] = 1.0           # org 10
    b = v.copy(); b[0] = 0.9           # org 20 (closest to query but wrong org)
    conn.execute("INSERT INTO knowledge_chunk_vectors(chunk_id,embedding,org_id,type,parent_id) "
                 "VALUES (1,?,10,'policy',100)", (_vec(a),))
    conn.execute("INSERT INTO knowledge_chunk_vectors(chunk_id,embedding,org_id,type,parent_id) "
                 "VALUES (2,?,20,'policy',200)", (_vec(b),))
    q = v.copy(); q[0] = 0.95
    rows = conn.execute(
        "SELECT chunk_id, parent_id FROM knowledge_chunk_vectors "
        "WHERE org_id = 10 AND embedding MATCH ? ORDER BY distance LIMIT 5", (_vec(q),)
    ).fetchall()
    assert [r[0] for r in rows] == [1]
    assert rows[0][1] == 100           # parent_id aux retrievable
