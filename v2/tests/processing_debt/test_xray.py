import sys, types as _t
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
import eval.processing_debt.xray as X

class _Chunk:
    def __init__(self, item_id, ce=None):
        self.item_id = item_id; self.ce_score = ce; self.content = f"c{item_id}"

def test_xray_assembles_pool_and_top5(monkeypatch):
    monkeypatch.setattr(X, "_route", lambda conn, q: _t.SimpleNamespace(skill="people_by_role"))
    monkeypatch.setattr(X, "_fused_pool", lambda conn, q, emb: [_Chunk(10), _Chunk(11), _Chunk(12)])
    monkeypatch.setattr(X, "_reranked", lambda conn, q, emb, rer: [_Chunk(11, 0.9), _Chunk(10, 0.4)])
    xr = X.xray("conn", "who is x", embedder="E", reranker="R")
    assert xr.fused_pool_ids == [10, 11, 12]
    assert xr.top5_ids == [11, 10]
    assert xr.ce_scores[11] == 0.9
    assert xr.router_skill == "people_by_role"
    assert xr.router_family is None
    assert xr.tier_primary_miss is False

def test_xray_primary_miss_when_empty(monkeypatch):
    monkeypatch.setattr(X, "_route", lambda conn, q: _t.SimpleNamespace(skill=None))
    monkeypatch.setattr(X, "_fused_pool", lambda conn, q, emb: [])
    monkeypatch.setattr(X, "_reranked", lambda conn, q, emb, rer: [])
    xr = X.xray("conn", "q", embedder="E", reranker="R")
    assert xr.top5_ids == [] and xr.tier_primary_miss is True and xr.router_skill is None
