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


from v2.integration.retriever_shim import V2RetrieverShim


class _ExplodingReranker:
    available = True
    def score(self, query, passages):
        raise AssertionError("top_relevance must NOT re-run the cross-encoder when ce_score is present")


class _FakeV1:
    def __init__(self, ce):
        self.text = "some body text"
        self.metadata = {"ce_score": ce}


def test_top_relevance_reuses_ce_score_without_second_pass():
    shim = object.__new__(V2RetrieverShim)
    shim.reranker = _ExplodingReranker()
    assert shim.top_relevance("q", [_FakeV1(0.91)]) == 0.91


def test_top_relevance_falls_back_when_no_ce_score():
    shim = object.__new__(V2RetrieverShim)
    shim.reranker = type("R", (), {"available": True,
                                   "score": staticmethod(lambda q, p: [0.42])})()
    assert shim.top_relevance("q", [_FakeV1(None)]) == 0.42
