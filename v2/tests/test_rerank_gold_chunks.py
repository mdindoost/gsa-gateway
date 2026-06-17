"""Acceptance gate (senior review C1): deterministic, chunk-level, no LLM. With the real
reranker ON, every GOLD fact must surface in the top-`limit` retrieved chunks, and no GUARD
fact may regress. Marked slow (downloads the model once)."""
import pytest

from v2.core.database.schema import get_connection
from v2.core.retrieval.retriever import V2Retriever
from v2.core.retrieval.embedder import Embedder
from v2.core.retrieval.reranker import CrossEncoderReranker
from v2.tests.rerank_gold import GOLD, GUARD

LIMIT = 5


def _hits(retr, query, substr):
    return any(substr.lower() in (c.content or "").lower()
               for c in retr.retrieve(query, limit=LIMIT))


@pytest.fixture(scope="module")
def reranked():
    conn = get_connection("gsa_gateway.db")
    return V2Retriever(conn, Embedder(), reranker=CrossEncoderReranker())


@pytest.mark.slow
@pytest.mark.parametrize("q,sub", list(GOLD.items()))
def test_gold_fact_in_top_k_with_rerank(reranked, q, sub):
    assert _hits(reranked, q, sub), f"GOLD miss after rerank: {q!r} (want {sub!r})"


@pytest.mark.slow
@pytest.mark.parametrize("q,sub", list(GUARD.items()))
def test_guard_fact_not_regressed(reranked, q, sub):
    assert _hits(reranked, q, sub), f"GUARD regressed after rerank: {q!r} (want {sub!r})"
