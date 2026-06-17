import pytest
from v2.core.database.schema import get_connection
from v2.core.retrieval.retriever import V2Retriever
from v2.core.retrieval.embedder import Embedder
from v2.core.retrieval.reranker import CrossEncoderReranker
from v2.tests.office_gold import OFFICE_GOLD, GUARD

TOP = 2  # "which ONE office" is a router answer — gold must be rank 1 or 2


@pytest.fixture(scope="module")
def retr():
    conn = get_connection("gsa_gateway.db")
    return V2Retriever(conn, Embedder(), reranker=CrossEncoderReranker())


@pytest.mark.slow
@pytest.mark.parametrize("q,token", list(OFFICE_GOLD.items()))
def test_office_in_top2(retr, q, token):
    chunks = retr.retrieve(q, limit=TOP)
    assert any(token.lower() in (c.content or "").lower() for c in chunks), \
        f"{q!r} -> want {token!r} in top-{TOP}; got {[c.title for c in chunks]}"


@pytest.mark.slow
@pytest.mark.parametrize("q,token", list(GUARD.items()))
def test_no_regression(retr, q, token):
    chunks = retr.retrieve(q, limit=5)
    assert any(token.lower() in (c.content or "").lower() for c in chunks), \
        f"regression: {q!r} -> want {token!r}"
