"""Tests for routing-slot scoring (slot-F1 gate)."""
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
from v2.eval.router.slot_metrics import slot_score, _pairset


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    njit = ensure_org(c, "njit", "NJIT", None, type="university")
    ywcc = ensure_org(c, "ywcc", "Ying Wu College of Computing", "njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", "ywcc", type="department")
    mie = ensure_org(c, "mie", "Mechanical and Industrial Engineering", "njit", type="department")
    c.execute("UPDATE organizations SET metadata=? WHERE slug='ywcc'", ('{"aliases": ["computing"]}',))
    c.execute("UPDATE organizations SET metadata=? WHERE slug='mie'",
              ('{"aliases": ["mechanical engineering", "mechanical"]}',))
    sync_org_nodes(c)
    project_appointment(c, person_key="d/koutis", name="Ioannis Koutis", org_id=cs,
                        category="faculty", titles=["Professor"], source_section="m", source="dashboard")
    c.commit()
    yield c
    c.close()


def test_org_matched_by_resolved_id_not_surface(conn):
    # gold "mie" vs pred "mechanical engineering" — same org, different surface → must count equal
    r = slot_score([("top_people_by_metric",
                     {"org": "mie", "metric": "citations"},
                     {"org": "mechanical engineering", "metric": "citations"})], conn)
    assert r["slot_f1"] == 1.0 and r["slot_exact_match"] == 1.0


def test_person_matched_by_resolved_id(conn):
    r = slot_score([("entity_card", {"person": "Koutis"}, {"person": "Ioannis Koutis"})], conn)
    assert r["slot_f1"] == 1.0


def test_missing_slot_is_recall_miss(conn):
    # gold has org, pred dropped it → recall < 1 (the "which prof does ML in computing" failure mode)
    r = slot_score([("people_by_research_area",
                     {"area": "machine learning", "org": "computing"},
                     {"area": "machine learning"})], conn)
    assert r["slot_recall"] < 1.0 and r["slot_precision"] == 1.0


def test_spurious_slot_is_precision_miss(conn):
    # pred added order/n the gold never had → precision < 1
    r = slot_score([("people_by_research_area",
                     {"area": "machine learning"},
                     {"area": "machine learning", "order": "desc", "n": 1})], conn)
    assert r["slot_precision"] < 1.0 and r["slot_recall"] == 1.0


def test_annotation_keys_ignored(conn):
    # gold carries a non-routing "note" key — must not be scored
    r = slot_score([("people_in_org",
                     {"org": "cs", "note": "some annotation"},
                     {"org": "cs"})], conn)
    assert r["slot_exact_match"] == 1.0


def test_routed_to_rag_scores_zero_recall(conn):
    # pred filled nothing (routed to RAG) → all gold slots are false negatives
    r = slot_score([("metric_of_person",
                     {"person": "Koutis", "metric": "citations"}, {})], conn)
    assert r["slot_recall"] == 0.0


def test_pairset_uses_only_routing_keys(conn):
    ps = _pairset(conn, {"org": "cs", "note": "x", "expected": "y", "n": 5})
    keys = {k for k, _ in ps}
    assert keys == {"org", "n"}
