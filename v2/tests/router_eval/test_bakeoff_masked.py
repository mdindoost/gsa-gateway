import sqlite3
from v2.eval.router.types import LabeledExample
from v2.eval.router.encode import FakeEncoder
from v2.eval.router.mask import SlotMasker
from v2.eval.router.bakeoff import run_bakeoff, format_report


def _data():
    ex = []
    for i, org in enumerate(["cs", "math", "bio", "chem", "me", "ywcc"]):
        ex.append(LabeledExample(f"f{i}", f"who teaches in {org}", "KG", "faculty_in_department",
                                 slots={"org": org}, group=f"fac-{org}"))
        ex.append(LabeledExample(f"o{i}", f"officers of {org}", "KG", "officers_in_org",
                                 slots={"org": org}, group=f"off-{org}"))
    return ex


def test_entity_split_with_masked_arms(monkeypatch):
    import v2.core.retrieval.router as srouter
    monkeypatch.setattr(srouter, "route", lambda conn, q: None)
    masker = SlotMasker(org_terms=["cs", "math", "bio", "chem", "me", "ywcc"], person_terms=[])
    conn = sqlite3.connect(":memory:")
    res = run_bakeoff(_data(), conn, FakeEncoder(16), test_frac=0.34, seed=1,
                      masker=masker, split_mode="entity")
    assert res["_meta"]["split_mode"] == "entity"
    # masked + abstaining arms present and gated
    assert {"masked_coarse", "masked_full", "masked_full_abstain"} <= set(res)
    assert "masked_full" in res["gate"]
    assert isinstance(format_report(res), str)


def test_default_no_masker_keeps_base_arms(monkeypatch):
    import v2.core.retrieval.router as srouter
    monkeypatch.setattr(srouter, "route", lambda conn, q: None)
    conn = sqlite3.connect(":memory:")
    res = run_bakeoff(_data(), conn, FakeEncoder(16), test_frac=0.34, seed=1)
    assert "masked_full" not in res            # no masker -> no masked arms
    assert {"detector_first", "coarse_then_deterministic", "full_classifier", "gate"} <= set(res)
