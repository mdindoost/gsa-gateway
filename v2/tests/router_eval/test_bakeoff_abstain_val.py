import sqlite3
from v2.eval.router.types import LabeledExample
from v2.eval.router.encode import FakeEncoder
from v2.eval.router.mask import SlotMasker
from v2.eval.router.bakeoff import run_bakeoff


def _data():
    ex = []
    for i, org in enumerate(["cs", "math", "bio", "chem", "me", "ywcc", "ece", "ds"]):
        ex.append(LabeledExample(f"f{i}", f"who teaches in {org}", "KG", "faculty_in_department",
                                 slots={"org": org}, group=f"fac-{org}"))
        ex.append(LabeledExample(f"g{i}", f"tell me about {org} life", "RAG", source="general",
                                 group=f"gen-{org}"))
    return ex


def test_val_frac_adds_family_abstaining_arm(monkeypatch):
    import v2.core.retrieval.router as srouter
    monkeypatch.setattr(srouter, "route", lambda conn, q: None)
    masker = SlotMasker(org_terms=["cs", "math", "bio", "chem", "me", "ywcc", "ece", "ds"],
                        person_terms=[])
    conn = sqlite3.connect(":memory:")
    res = run_bakeoff(_data(), conn, FakeEncoder(16), test_frac=0.3, seed=1,
                      masker=masker, split_mode="entity", val_frac=0.3)
    assert "masked_coarse_abstain" in res
    assert "family_abstention_margin" in res["_meta"]
    assert res["_meta"]["n_val"] >= 1


def test_no_val_frac_keeps_today_behavior(monkeypatch):
    import v2.core.retrieval.router as srouter
    monkeypatch.setattr(srouter, "route", lambda conn, q: None)
    conn = sqlite3.connect(":memory:")
    res = run_bakeoff(_data(), conn, FakeEncoder(16), test_frac=0.3, seed=1)
    assert "masked_coarse_abstain" not in res
    assert res["_meta"].get("n_val", 0) == 0
