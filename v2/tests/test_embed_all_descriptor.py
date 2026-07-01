"""embed_all.py (the item-level batch embed) is descriptor-driven: the asymmetric prefix
and vector dimension come from the active ModelDescriptor, not hardcoded nomic constants."""
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import v2.scripts.embed_all as ea


def test_embed_document_raw_and_query_wrapped_for_qwen(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    box = {}
    monkeypatch.setattr(ea, "_post_embed",
                        lambda text, timeout=30: box.__setitem__("t", text) or [0.0] * 1024)
    ea.embed_document("The Registrar processes holds.")
    assert box["t"] == "The Registrar processes holds."        # passage embedded raw
    ea.embed_query("who handles admissions")
    assert box["t"] == (
        "Instruct: Given a web search query, retrieve relevant passages "
        "that answer the query\nQuery: who handles admissions"
    )


def test_embed_document_nomic_prefix(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "nomic-embed-text")
    box = {}
    monkeypatch.setattr(ea, "_post_embed",
                        lambda text, timeout=30: box.__setitem__("t", text) or [0.0] * 768)
    ea.embed_document("hi")
    assert box["t"] == "search_document: hi"
    ea.embed_query("hi")
    assert box["t"] == "search_query: hi"


def test_post_embed_uses_active_model_name(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    sent = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"embeddings": [[0.0]]}'

    def fake_urlopen(req, timeout=30):
        import json as _j
        sent["model"] = _j.loads(req.data)["model"]
        return _Resp()

    monkeypatch.setattr(ea.urllib.request, "urlopen", fake_urlopen)
    ea._post_embed("x")
    assert sent["model"] == "qwen3-embedding:0.6b"
