import sqlite3, json, importlib
import v2.core.retrieval.area_expand as ax


def _fx():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE knowledge_items(id INTEGER PRIMARY KEY,type TEXT,is_active INT,metadata TEXT)")
    for i, t in enumerate(["cyber security", "network security"]):
        c.execute("INSERT INTO knowledge_items(type,is_active,metadata) VALUES('research_areas',1,?)",
                  (json.dumps({"entity_id": f"e{i}", "areas": [t]}),))
    c.commit()
    return c


class Stub:
    def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, t):
        return [1.0, 0.0]


def test_disabled_returns_empty(monkeypatch):
    monkeypatch.setenv("AREA_EXPAND_ENABLED", "0")
    importlib.reload(ax)
    assert ax.expand_area_llm(_fx(), "cyber security", embedder=Stub(),
                               verify=lambda *a: {"indices": [1, 2]}) == set()


def test_enabled_returns_verified(monkeypatch):
    monkeypatch.setenv("AREA_EXPAND_ENABLED", "1")
    importlib.reload(ax)
    monkeypatch.setattr(ax, "area_cache", __import__("types").SimpleNamespace(
        get=lambda k: None, put=lambda k, v: None, get_blob=lambda n: None, put_blob=lambda n, d: None))
    out = ax.expand_area_llm(_fx(), "cyber security", embedder=Stub(),
                              verify=lambda *a: {"indices": [1, 2]})
    assert out == {"cyber security", "network security"}


def test_blank_area_returns_empty(monkeypatch):
    monkeypatch.setenv("AREA_EXPAND_ENABLED", "1")
    importlib.reload(ax)
    assert ax.expand_area_llm(_fx(), "   ", embedder=Stub(),
                               verify=lambda *a: {"indices": [1, 2]}) == set()


def test_cache_hit_skips_candidate_and_verify(monkeypatch):
    monkeypatch.setenv("AREA_EXPAND_ENABLED", "1")
    importlib.reload(ax)
    monkeypatch.setattr(ax, "area_cache", __import__("types").SimpleNamespace(
        get=lambda k: ["cyber security"], put=lambda k, v: (_ for _ in ()).throw(AssertionError("put should not be called on hit")),
        get_blob=lambda n: None, put_blob=lambda n, d: None))

    def _boom(*a, **kw):
        raise AssertionError("candidate_tags/verify should not run on cache hit")

    monkeypatch.setattr(ax, "candidate_tags", _boom)
    out = ax.expand_area_llm(_fx(), "cyber security", embedder=Stub(),
                              verify=lambda *a: {"indices": [1, 2]})
    assert out == {"cyber security"}


def test_error_falls_back_to_empty_set(monkeypatch):
    monkeypatch.setenv("AREA_EXPAND_ENABLED", "1")
    importlib.reload(ax)

    def _boom(conn):
        raise RuntimeError("boom")

    monkeypatch.setattr(ax, "vocab_signature", _boom)
    out = ax.expand_area_llm(_fx(), "cyber security", embedder=Stub(),
                              verify=lambda *a: {"indices": [1, 2]})
    assert out == set()
