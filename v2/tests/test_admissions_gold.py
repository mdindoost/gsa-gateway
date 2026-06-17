import pytest
from v2.core.database.schema import get_connection
from v2.core.retrieval.retriever import V2Retriever
from v2.core.retrieval.embedder import Embedder
from v2.core.retrieval.reranker import CrossEncoderReranker
from v2.tests.admissions_gold import ADM_GOLD, GUARD


@pytest.fixture(scope="module")
def retr():
    conn = get_connection("gsa_gateway.db")
    return V2Retriever(conn, Embedder(), reranker=CrossEncoderReranker())


def _hits(retr, q, token, k):
    return any(token.lower() in (c.content or "").lower() for c in retr.retrieve(q, limit=k))


@pytest.mark.slow
@pytest.mark.parametrize("q,token", list(ADM_GOLD.items()))
def test_admissions_topic_in_top2(retr, q, token):
    assert _hits(retr, q, token, 2), f"{q!r} -> want {token!r} in top-2"


@pytest.mark.slow
@pytest.mark.parametrize("q,token", list(GUARD.items()))
def test_no_regression(retr, q, token):
    assert _hits(retr, q, token, 5), f"regression {q!r} -> want {token!r}"
