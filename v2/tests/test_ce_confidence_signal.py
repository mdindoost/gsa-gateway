from v2.core.database.schema import get_connection
from v2.core.retrieval.retriever import RetrievedChunk, V2Retriever
from v2.core.retrieval.embedder import Embedder


def test_retrieved_chunk_has_ce_score_default_none():
    c = RetrievedChunk(
        item_id=1, title="t", type="policy", content="body",
        org_path="NJIT > GSA", similarity=0.5, source="hybrid", rrf_score=0.1,
    )
    assert c.ce_score is None
    c.ce_score = 0.83
    assert c.ce_score == 0.83


class _StubReranker:
    available = True
    def __init__(self, target): self.target = target.lower()
    def score(self, query, passages):
        return [1.0 if self.target in (p or "").lower() else 0.0 for p in passages]


def test_retrieve_attaches_matched_chunk_ce_score():
    conn = get_connection("gsa_gateway.db")
    q = "What is the maximum GSA travel award per fiscal year?"
    rr = V2Retriever(conn, Embedder(), reranker=_StubReranker("travel award"))
    chunks = rr.retrieve(q, limit=5)
    # at least one reranked chunk carries a real CE score (not None)
    assert any(c.ce_score is not None for c in chunks)
    # the stub scores are exactly 0.0/1.0
    assert all(c.ce_score in (0.0, 1.0) for c in chunks if c.ce_score is not None)


def test_retrieve_ce_score_none_when_no_reranker():
    conn = get_connection("gsa_gateway.db")
    rr = V2Retriever(conn, Embedder(), reranker=None)
    chunks = rr.retrieve("What is the maximum GSA travel award per fiscal year?", limit=5)
    assert all(c.ce_score is None for c in chunks)
