import sys, sqlite3
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.types import PresenceResult, PresenceEvidence, XRay
from eval.processing_debt.attribute import attribute

def _xray(pool, top5, skill=None):
    return XRay("q", None, skill, pool, top5, {i: 0.5 for i in top5}, False, "answer text")

def _pres(evs):
    return PresenceResult(True, sorted({e.probe for e in evs}), evs)

def _ki(item_id, item_type="policy", probe="fts_probe"):
    return PresenceEvidence("knowledge_item", str(item_id), "span", probe, item_type=item_type)

def _node(node_id=1):
    return PresenceEvidence("node", str(node_id), "Pan Xu | ...", "kg_probe")

def test_config_when_only_excluded_publication():
    a = attribute(None, "fact", _pres([_ki(11, "publication")]), _xray([10], [10]))
    assert a.stage == "CONFIG"

def test_pool_when_evidence_chunk_absent_from_pool():
    a = attribute(None, "fact", _pres([_ki(99)]), _xray([10, 11], [10, 11]))
    assert a.stage == "POOL"

def test_rank_when_in_pool_below_top5_and_chunk_yields():
    a = attribute(None, "fact", _pres([_ki(12)]), _xray([10, 11, 12], [10, 11]),
                  erag=lambda conn, iid, q, f: True)
    assert a.stage == "RANK"

def test_pool_when_in_pool_below_top5_but_chunk_not_utile():
    a = attribute(None, "fact", _pres([_ki(12)]), _xray([10, 11, 12], [10, 11]),
                  erag=lambda conn, iid, q, f: False)
    assert a.stage == "POOL"

def test_compose_when_in_top5_but_missing_from_answer():
    a = attribute(None, "fact", _pres([_ki(10)]), _xray([10, 11], [10, 11]))
    assert a.stage == "COMPOSE"

def test_router_when_kg_only_and_router_not_structured():
    a = attribute(None, "fact", _pres([_node(1)]), _xray([], [], skill=None))
    assert a.stage == "ROUTER"

def test_router_precision_prefers_servable_chunk_over_router():
    # node evidence AND a servable chunk, router missed → NOT router; the servable chunk governs
    a = attribute(None, "fact", _pres([_node(1), _ki(10)]), _xray([10], [10], skill=None))
    assert a.stage == "COMPOSE"

def test_config_reads_live_exclude_types_from_settings():   # M4 regression guard
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE settings(key TEXT, value TEXT)")
    conn.execute("INSERT INTO settings VALUES('retriever.exclude_types','news,events')")
    conn.commit()
    a = attribute(conn, "fact", _pres([_ki(5, "news")]), _xray([10], [10]))
    assert a.stage == "CONFIG"    # 'news' is excluded per the live setting (not the hardcoded default)
