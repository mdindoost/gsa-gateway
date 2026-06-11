"""doc_id pass-through (shim) + the optional retrieval debug trace."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import v2.core.retrieval.retriever as rt
from v2.core.database.schema import create_all
from v2.integration.retriever_shim import V2RetrieverShim


def test_shim_carries_item_id():
    c = SimpleNamespace(content="x", org_path="NJIT > YWCC > Computer Science",
                        type="contact", title="Baruch Schieber", similarity=0.7,
                        source="hybrid", item_id=170)
    v1 = V2RetrieverShim._to_v1(c)
    assert v1.item_id == 170
    assert v1.source_file == "Computer Science"   # last org-path segment


def test_retrieval_trace_writes_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "RETRIEVAL_DEBUG_FILE", tmp_path / "trace.log")
    monkeypatch.setenv("RETRIEVAL_DEBUG_LOG", "true")
    r = rt.V2Retriever(create_all(":memory:"), embedder=None)
    assert r.debug_log is True
    chunk = rt.RetrievedChunk(item_id=170, title="Baruch Schieber", type="contact",
                              content="…", org_path="NJIT > YWCC > Computer Science",
                              similarity=0.71, source="hybrid", rrf_score=0.0468)
    r._write_trace("does schieber do graphs?", None, 40, 40, [chunk])
    text = (tmp_path / "trace.log").read_text()
    assert "QUERY:" in text and "doc_id=170" in text
    assert "[contact]" in text and "leg=hybrid" in text and "rrf+boost=0.0468" in text


def test_retrieval_trace_off_by_default(monkeypatch):
    monkeypatch.delenv("RETRIEVAL_DEBUG_LOG", raising=False)
    r = rt.V2Retriever(create_all(":memory:"), embedder=None)
    assert r.debug_log is False
