# v2/tests/processing_debt/test_report.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.types import PresenceResult, FactRecord
from eval.processing_debt.report import (
    build_report, render_md, bootstrap_debt_ci, required_n,
    questions_for_margin, power_analysis, sc6_oracle_correctness, compare_sets,
    debt_at_threshold)


def _rec(cls, stage=None, vital=True, stratum="db_rag", q="q", guard="supported",
         low_conf=False, max_score=None):
    if max_score is None:
        max_score = 0.9 if cls == "OWNED_NOT_SURFACED" else (0.42 if low_conf else 0.0)
    pres = PresenceResult(cls == "OWNED_NOT_SURFACED", [], [], low_conf=low_conf, max_score=max_score)
    return FactRecord(q, stratum, "f", vital, guard, cls == "IN_ANSWER",
                      pres, cls, stage, q, judge_id="nli", max_score=max_score)


# --- original 3 (unchanged behavior) ---
def test_debt_ratio_and_stage_table():
    recs = [_rec("IN_ANSWER"), _rec("OWNED_NOT_SURFACED", "POOL"),
            _rec("OWNED_NOT_SURFACED", "COMPOSE"), _rec("NOT_OWNED")]
    rep = build_report(recs, {"in_answer": 0.7, "presence": 0.65})
    assert abs(rep["processing_debt"] - (2/3)) < 1e-6
    assert rep["stage_counts"]["POOL"] == 1 and rep["stage_counts"]["COMPOSE"] == 1

def test_sc1_gate_pass_when_kappa_high():
    rep = build_report([_rec("OWNED_NOT_SURFACED", "POOL")] * 5, {"in_answer": 0.7, "presence": 0.7})
    assert rep["SC1"] is True

def test_sc1_gate_fail_when_kappa_low():
    rep = build_report([_rec("OWNED_NOT_SURFACED", "POOL")], {"in_answer": 0.3, "presence": 0.7})
    assert rep["SC1"] is False


# --- SC6 oracle-correctness (R9) ---
def test_sc6_counts_dropped_without_double_counting():
    recs = [_rec("IN_ANSWER"), _rec("OWNED_NOT_SURFACED", "POOL"),
            _rec("DROPPED_ORACLE", guard="unsupported"),
            _rec("DROPPED_ORACLE", guard="we_are_authority")]
    s = sc6_oracle_correctness([r for r in recs if r.vital])
    assert s["n_guarded"] == 4 and s["n_dropped"] == 2
    assert s["n_unsupported"] == 1 and s["n_authority"] == 1
    assert abs(s["rate"] - 0.5) < 1e-9 and s["gate_pass"] is False   # 50% > 30% → fail
    rep = build_report(recs, {"in_answer": 0.7, "presence": 0.7})
    assert rep["SC6"] is False and abs(rep["sc6_rate"] - 0.5) < 1e-9

def test_sc6_gate_passes_below_threshold():
    recs = [_rec("IN_ANSWER")] * 9 + [_rec("DROPPED_ORACLE", guard="unsupported")]
    rep = build_report(recs, {"in_answer": 0.7, "presence": 0.7})
    assert abs(rep["sc6_rate"] - 0.1) < 1e-9 and rep["SC6"] is True   # 10% <= 30%


# --- bootstrap CI + required_n + cluster-consistent questions (R13 + B3) ---
def test_bootstrap_ci_brackets_point():
    # 4 questions, 1 owned-miss each of 2 facts -> debt 0.5 exactly, CI brackets it
    recs = []
    for i in range(4):
        recs.append(_rec("OWNED_NOT_SURFACED", "POOL", q=f"q{i}"))
        recs.append(_rec("IN_ANSWER", q=f"q{i}"))
    point, lo, hi = bootstrap_debt_ci(recs, iters=500)
    assert abs(point - 0.5) < 1e-9 and lo <= point <= hi

def test_required_n_math():
    r = required_n(0.3, facts_per_q=4.0, target_margin=0.05)
    assert r["facts_needed"] == round((1.96**2 * 0.3*0.7) / 0.05**2)
    assert r["questions_needed"] == round(r["facts_needed"] / 4.0)

def test_questions_for_margin_scales_with_width():
    # tighter target margin (vs the observed half-width) needs more questions
    n_small = questions_for_margin(0.10, 50, 0.10)     # target == half-width -> ~50
    n_tight = questions_for_margin(0.10, 50, 0.05)     # halve the margin -> ~4x
    assert n_small == 50 and n_tight == 200

def test_power_analysis_shape():
    recs = []
    for i in range(6):
        recs.append(_rec("OWNED_NOT_SURFACED", "POOL", q=f"q{i}"))
        recs.append(_rec("IN_ANSWER", q=f"q{i}"))
    pa = power_analysis(recs)
    assert "debt_point" in pa and "ci_lo" in pa and "ci_hi" in pa and "half_width" in pa
    assert pa["n_questions"] == 6 and pa["n_owned_vital"] == 12
    assert "0.05" in pa["targets"] and "questions_needed" in pa["targets"]["0.05"]
    assert "POOL" in pa["per_stage"]


# --- B4 low-denominator suppression ---
def test_b4_suppresses_low_denominator_debt():
    recs = [_rec("OWNED_NOT_SURFACED", "POOL"), _rec("IN_ANSWER")]   # denom 2 < 20
    rep = build_report(recs, {"in_answer": 0.7, "presence": 0.7}, set_name="C")
    assert rep["debt_reportable"] is False
    md = render_md(rep)
    assert "not a debt estimate" in md
    assert "%" not in md.split("Processing Debt")[1].split("\n")[0]   # no debt % on the headline line

def test_debt_reportable_true_when_denom_ge_20():
    recs = [_rec("OWNED_NOT_SURFACED", "POOL", q=f"q{i}") for i in range(10)] + \
           [_rec("IN_ANSWER", q=f"q{i}") for i in range(10)]           # denom 20
    rep = build_report(recs, {"in_answer": 0.7, "presence": 0.7})
    assert rep["debt_reportable"] is True


# --- unsure-rate + nugget-quality rendering (R9 / R14 minor) ---
def test_render_includes_unsure_and_nugget_quality():
    rep = build_report([_rec("OWNED_NOT_SURFACED", "POOL")] * 5, {"in_answer": 0.7, "presence": 0.7},
                       nugget_quality={"precision": 0.9, "recall": 0.8, "accepted": 18,
                                       "total_machine": 20, "added": 4, "rejected": 2},
                       unsure_rates={"in_answer": 0.15, "presence": 0.22})
    md = render_md(rep)
    assert "unsure" in md.lower() and "0.15" in md
    assert "precision" in md.lower() and "recall" in md.lower()


# --- per-set comparison (R9) with B4 asterisk ---
def test_compare_sets_table_asterisks_low_denom():
    a = build_report([_rec("OWNED_NOT_SURFACED", "POOL", q=f"a{i}") for i in range(12)] +
                     [_rec("IN_ANSWER", q=f"a{i}") for i in range(12)], {"in_answer": 0.7, "presence": 0.7},
                     set_name="A")
    c = build_report([_rec("OWNED_NOT_SURFACED", "POOL", q="c0"), _rec("IN_ANSWER", q="c1")],
                     {"in_answer": 0.7, "presence": 0.7}, set_name="C")
    tbl = compare_sets({"A": a, "C": c})
    assert "A" in tbl and "C" in tbl and "*" in tbl        # low-denom set flagged


def test_questions_for_margin_floors_at_observed():
    # target looser than the observed half-width -> you already have enough; never suggest < observed
    assert questions_for_margin(0.05, 50, 0.10) == 50


def test_per_stage_n_positive_when_stage_dominant():
    recs = [_rec("OWNED_NOT_SURFACED", "POOL", q=f"q{i}") for i in range(3)] + \
           [_rec("IN_ANSWER", q=f"q{i}") for i in range(3)]
    pa = power_analysis(recs)          # POOL owns 100% of misses
    n = pa["per_stage"]["POOL"]["questions_for_0.10"]
    assert n is not None and n > 0     # not the degenerate ~0


# --- judge-fix buckets: low-conf + non-self-contained + not-owned counts (Fable B2 / pronoun) ---
def test_report_counts_low_conf_and_non_self_contained():
    recs = [_rec("IN_ANSWER"),
            _rec("OWNED_NOT_SURFACED", "POOL"),
            _rec("NOT_OWNED", low_conf=True, max_score=0.42),   # surfaced low-conf bucket
            _rec("NOT_OWNED", max_score=0.1),                   # true gap
            _rec("NON_SELF_CONTAINED")]
    rep = build_report(recs, {"in_answer": 0.7, "presence": 0.7})
    assert rep["n_not_owned"] == 2
    assert rep["n_low_conf"] == 1
    assert rep["n_non_self_contained"] == 1
    # non-self-contained excluded from the debt denominator
    assert rep["denom"] == 2   # 1 IN_ANSWER + 1 OWNED_NOT_SURFACED

def test_render_shows_low_conf_and_non_self_contained_buckets():
    recs = [_rec("IN_ANSWER")] * 10 + [_rec("OWNED_NOT_SURFACED", "POOL", q=f"q{i}") for i in range(12)] + \
           [_rec("NOT_OWNED", low_conf=True, max_score=0.4), _rec("NON_SELF_CONTAINED")]
    md = render_md(build_report(recs, {"in_answer": 0.7, "presence": 0.7}))
    assert "low-conf" in md.lower() or "low confidence" in md.lower()
    assert "non-self-contained" in md.lower()


# --- threshold sensitivity from stored max_score (Fable: report HI in {0.4,0.5,0.6}) ---
def test_debt_at_threshold_moves_with_hi():
    # 1 in-answer + presence pop with scores 0.45, 0.55, 0.95
    recs = [_rec("IN_ANSWER"),
            _rec("OWNED_NOT_SURFACED", "POOL", max_score=0.95),
            _rec("OWNED_NOT_SURFACED", "POOL", max_score=0.55),
            _rec("NOT_OWNED", low_conf=True, max_score=0.45)]
    # HI=0.6 -> only the 0.95 fact is owned -> debt 1/(1+1)=0.5
    assert abs(debt_at_threshold(recs, 0.6) - 0.5) < 1e-9
    # HI=0.5 -> 0.95 & 0.55 owned -> debt 2/(1+2)
    assert abs(debt_at_threshold(recs, 0.5) - (2/3)) < 1e-9
    # HI=0.4 -> all three -> debt 3/(1+3)
    assert abs(debt_at_threshold(recs, 0.4) - 0.75) < 1e-9

def test_report_includes_debt_sensitivity_band():
    recs = [_rec("IN_ANSWER")] * 5 + [_rec("OWNED_NOT_SURFACED", "POOL", q=f"q{i}", max_score=0.7) for i in range(20)]
    rep = build_report(recs, {"in_answer": 0.7, "presence": 0.7})
    assert set(rep["debt_sensitivity"]) >= {"0.4", "0.5", "0.6"}
    assert "sensitivity" in render_md(rep).lower()
