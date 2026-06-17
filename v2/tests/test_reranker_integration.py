from v2.core.database.schema import get_connection
from v2.core.retrieval.retriever import V2Retriever
from v2.core.retrieval.embedder import Embedder


class _StubReranker:
    """Scores by whether a target substring is present — deterministic, no model."""
    available = True

    def __init__(self, target):
        self.target = target.lower()

    def score(self, query, passages):
        return [1.0 if self.target in (p or "").lower() else 0.0 for p in passages]


def test_rerank_lifts_target_chunk_to_top():
    conn = get_connection("gsa_gateway.db")
    q = "Who chairs the GSA General Assembly meetings?"
    target = "Chair the General Assembly meetings"

    rr = V2Retriever(conn, Embedder(), reranker=_StubReranker(target))
    rr_top = rr.retrieve(q, limit=1)[0]
    assert target.lower() in (rr_top.content or "").lower()


def test_reranker_none_is_unchanged_behaviour():
    conn = get_connection("gsa_gateway.db")
    a = V2Retriever(conn, Embedder())
    b = V2Retriever(conn, Embedder(), reranker=None)
    q = "What is the maximum GSA travel award per fiscal year?"
    assert [c.item_id for c in a.retrieve(q, limit=5)] == [c.item_id for c in b.retrieve(q, limit=5)]
