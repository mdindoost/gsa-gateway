from unittest.mock import MagicMock
from v2.core.retrieval.retriever import V2Retriever


def _retriever_with_spy_embedder():
    r = V2Retriever.__new__(V2Retriever)          # bypass __init__/DB
    r.embedder = MagicMock()
    r.embedder.embed_query.return_value = [0.0] * 768
    return r


def test_supplied_query_vec_skips_embed(monkeypatch):
    r = _retriever_with_spy_embedder()
    # stub the legs so we only assert the encode decision
    monkeypatch.setattr(r, "_allowed_ids", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(r, "_semantic", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(r, "_keyword", lambda *a, **k: [], raising=False)
    r.conn = MagicMock()
    r.conn.execute.return_value.fetchone.return_value = [0]
    r.pool_size = 60
    r.exclude_types = []
    r.retrieve("cs faculty", query_vec=[0.1] * 768)
    r.embedder.embed_query.assert_not_called()


def test_no_query_vec_still_embeds(monkeypatch):
    r = _retriever_with_spy_embedder()
    monkeypatch.setattr(r, "_allowed_ids", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(r, "_semantic", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(r, "_keyword", lambda *a, **k: [], raising=False)
    r.conn = MagicMock()
    r.conn.execute.return_value.fetchone.return_value = [0]
    r.pool_size = 60
    r.exclude_types = []
    r.retrieve("cs faculty")
    r.embedder.embed_query.assert_called_once()
