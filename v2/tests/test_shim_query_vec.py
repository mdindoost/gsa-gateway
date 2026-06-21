from unittest.mock import MagicMock, patch
from v2.integration.retriever_shim import V2RetrieverShim


def test_shim_forwards_query_vec():
    shim = V2RetrieverShim.__new__(V2RetrieverShim)
    shim.db_path = ":memory:"
    shim.embedder = MagicMock()
    shim.org_id = None
    shim.reranker = None
    captured = {}
    with patch("v2.integration.retriever_shim.get_connection", return_value=MagicMock()), \
         patch("v2.integration.retriever_shim.V2Retriever") as RV:
        RV.return_value.retrieve.side_effect = lambda *a, **k: captured.update(k) or []
        shim._retrieve_sync("cs faculty", None, query_vec=[0.2] * 768)
    assert captured.get("query_vec") == [0.2] * 768
