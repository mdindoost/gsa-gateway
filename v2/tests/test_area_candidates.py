import sqlite3, json
import v2.core.retrieval.area_expand as ax


def _fx():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE knowledge_items(id INTEGER PRIMARY KEY,type TEXT,is_active INT,metadata TEXT)")
    for i, t in enumerate(["cyber security", "network security", "machine learning"]):
        c.execute("INSERT INTO knowledge_items(type,is_active,metadata) VALUES('research_areas',1,?)",
                  (json.dumps({"entity_id": f"e{i}", "areas": [t]}),))
    c.commit(); return c


class Stub:
    def embed_documents(self, texts): return [[1.0, 0.0] if "secur" in t else [0.0, 1.0] for t in texts]
    def embed_query(self, t): return [1.0, 0.0]        # near the security vectors


def test_candidates_union(monkeypatch):
    conn = _fx()
    cands = ax.candidate_tags(conn, "cyber security", k=1, embedder=Stub())
    # token-overlap guarantees BOTH security tags regardless of k=1 KNN
    assert "network security" in cands and "cyber security" in cands
