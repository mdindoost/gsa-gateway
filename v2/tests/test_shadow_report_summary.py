"""F1: the flip-gate report computes TRUE new-vs-current agreement from shadow records."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "router_v21_shadow_report",
    Path(__file__).resolve().parents[2] / "scripts" / "router_v21_shadow_report.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
summarize_shadow = _mod.summarize_shadow


def test_agreement_rate_and_disagreements():
    rows = [
        {"new_family": "KG", "current_family": "KG"},     # agree
        {"new_family": "KG", "current_family": "RAG"},     # disagree (legacy RAG → new KG)
        {"new_family": "RAG", "current_family": "RAG"},    # agree
        {"new_family": "COMMAND", "current_family": None},  # not comparable (old record)
    ]
    s = summarize_shadow(rows)
    assert s["total"] == 4
    assert s["comparable"] == 3
    assert abs(s["agreement_rate"] - 2 / 3) < 1e-9
    assert s["disagreements"] == {"RAG->KG": 1}
    assert s["new_family_hist"]["KG"] == 2


def test_no_comparable_records():
    s = summarize_shadow([{"new_family": "KG", "current_family": None}])
    assert s["comparable"] == 0 and s["agreement_rate"] is None
