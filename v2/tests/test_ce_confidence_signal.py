from v2.core.retrieval.retriever import RetrievedChunk


def test_retrieved_chunk_has_ce_score_default_none():
    c = RetrievedChunk(
        item_id=1, title="t", type="policy", content="body",
        org_path="NJIT > GSA", similarity=0.5, source="hybrid", rrf_score=0.1,
    )
    assert c.ce_score is None
    c.ce_score = 0.83
    assert c.ce_score == 0.83
