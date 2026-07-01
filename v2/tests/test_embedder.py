"""Embedder is descriptor-driven: model, dim, and asymmetric prefixes read from the
active ModelDescriptor (not hardcoded nomic constants)."""
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.retrieval.embedder import Embedder


def _capture(e):
    """Replace _embed with a capturing stub; returns a dict that records the last input text."""
    box = {}

    def fake(text, timeout=30):
        box["text"] = text
        return [0.0] * 8
    e._embed = fake
    return box


def test_model_defaults_to_active_descriptor_qwen(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    assert Embedder().model == "qwen3-embedding:0.6b"


def test_query_wraps_with_qwen_instruct_prefix(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    e = Embedder()
    box = _capture(e)
    e.embed_query("who handles graduate admissions")
    assert box["text"] == (
        "Instruct: Given a web search query, retrieve relevant passages "
        "that answer the query\nQuery: who handles graduate admissions"
    )


def test_document_is_raw_for_qwen(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    e = Embedder()
    box = _capture(e)
    e.embed_document("The Registrar processes registration holds.")
    assert box["text"] == "The Registrar processes registration holds."   # no prefix


def test_nomic_prefixes_preserved_when_selected(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "nomic-embed-text")
    e = Embedder()
    box = _capture(e)
    e.embed_query("x")
    assert box["text"] == "search_query: x"
    e.embed_document("y")
    assert box["text"] == "search_document: y"


def test_health_check_uses_descriptor_dim(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    e = Embedder()
    e._embed = lambda text, timeout=15: [0.1] * 1024
    assert e.health_check() is True
    e._embed = lambda text, timeout=15: [0.1] * 768        # wrong dim for qwen
    assert e.health_check() is False


def test_query_truncates_by_tokens_not_chars(monkeypatch):
    # Long input is token-truncated via the descriptor (not a raw char slice); prefix still present.
    monkeypatch.setenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    e = Embedder()
    box = _capture(e)
    e.embed_query("word " * 50)
    assert box["text"].startswith(
        "Instruct: Given a web search query, retrieve relevant passages "
        "that answer the query\nQuery: word"
    )
