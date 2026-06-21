import sqlite3
from v2.eval.router.types import LabeledExample
from v2.eval.router.encode import FakeEncoder
from v2.eval.router.bakeoff import run_bakeoff, format_report


def test_runs_all_arms_and_gates(monkeypatch):
    # stub the production router so the DetectorFirst/Coarse arms are deterministic & offline
    import v2.core.retrieval.router as srouter
    monkeypatch.setattr(srouter, "route", lambda conn, q: None)   # always abstains -> RAG
    ex = [LabeledExample(str(i), f"food query {i}", "RAG", source="food", group=f"g{i%3}")
          for i in range(12)] + \
         [LabeledExample(f"k{i}", f"who teaches dept {i}", "KG", "faculty_in_department",
                         group=f"kg{i%3}") for i in range(12)]
    conn = sqlite3.connect(":memory:")
    res = run_bakeoff(ex, conn, FakeEncoder(16), test_frac=0.3, seed=2)
    assert set(res) >= {"detector_first", "coarse_then_deterministic", "full_classifier", "gate"}
    assert "family_accuracy" in res["detector_first"]
    assert isinstance(format_report(res), str)
