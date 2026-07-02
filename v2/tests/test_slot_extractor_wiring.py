"""Wiring tests: UnifiedRouter.resolve_kg fallback seam + the sync Ollama JSON helper."""
from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
from v2.core.retrieval.unified_router import UnifiedRouter


@pytest.fixture()
def db_path(tmp_path):
    p = str(tmp_path / "kg.db")
    c = create_all(p)
    njit = ensure_org(c, "njit", "NJIT", None, type="university")
    ywcc = ensure_org(c, "ywcc", "Ying Wu College of Computing", "njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", "ywcc", type="department")
    c.execute("UPDATE organizations SET metadata=? WHERE slug='ywcc'", ('{"aliases": ["computing"]}',))
    sync_org_nodes(c)
    project_appointment(c, person_key="d/koutis", name="Ioannis Koutis", org_id=cs,
                        category="faculty", titles=["Professor"], source_section="m", source="dashboard")
    c.commit()
    c.close()
    return p


def _router(db_path, generate_json):
    # resolve_kg doesn't touch classifier/intent_detector, so stubs are fine here.
    return UnifiedRouter(db_path=db_path, classifier=None, intent_detector=None,
                         generate_json=generate_json)


def test_resolve_kg_uses_extractor_on_regex_miss(db_path):
    # "which prof does ML in computing" is a genuine route() miss (no area verb / faculty cue).
    stub = lambda s, p, sc: {"skill": "people_by_research_area",
                             "slots": {"area": "machine learning", "org": "computing"},
                             "confidence": 0.9}
    d = _router(db_path, stub).resolve_kg("which prof does ML in computing")
    assert d.family == "KG" and d.skill == "people_by_research_area"
    assert d.args["area"] == "machine learning"


def test_resolve_kg_no_generator_falls_to_rag(db_path):
    d = _router(db_path, None).resolve_kg("which prof does ML in computing")
    assert d.family == "RAG" and d.source == "general"


def test_resolve_kg_extractor_none_falls_to_rag(db_path):
    d = _router(db_path, lambda s, p, sc: {"skill": "none", "slots": {}, "confidence": 0}).resolve_kg(
        "which prof does ML in computing")
    assert d.family == "RAG"


def test_resolve_kg_unresolved_slot_falls_to_rag(db_path):
    # extractor confidently returns a person that isn't in the KG → must NOT execute → RAG.
    stub = lambda s, p, sc: {"skill": "entity_card", "slots": {"person": "Ghost Person"}, "confidence": 1}
    d = _router(db_path, stub).resolve_kg("some unmatched query xyz")
    assert d.family == "RAG"


def test_resolve_kg_regex_hit_bypasses_extractor(db_path):
    # A clear regex hit must NOT call the extractor (would raise if it did).
    def boom(s, p, sc):
        raise AssertionError("extractor should not run on a regex hit")
    d = _router(db_path, boom).resolve_kg("who is Ioannis Koutis")
    assert d.family == "KG" and d.skill == "entity_card"


# ── sync Ollama JSON helper ──────────────────────────────────────────────────────────────────────
def test_generate_json_sync_parses(monkeypatch):
    import json
    from bot.services import ollama_client

    class _Resp:
        status = 200
        def read(self): return json.dumps({"response": json.dumps({"skill": "none"})}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(ollama_client.urllib.request, "urlopen", lambda req, timeout: _Resp())
    out = ollama_client.generate_json_sync("sys", "prompt", {"type": "object"})
    assert out == {"skill": "none"}


def test_generate_json_sync_none_on_error(monkeypatch):
    from bot.services import ollama_client

    def _boom(req, timeout):
        raise OSError("connection refused")
    monkeypatch.setattr(ollama_client.urllib.request, "urlopen", _boom)
    assert ollama_client.generate_json_sync("sys", "prompt", {"type": "object"}) is None
