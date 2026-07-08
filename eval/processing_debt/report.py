# eval/processing_debt/report.py
"""Aggregate the pilot into a debt headline + SC1–SC6 gates + power analysis. Pure arithmetic; read-only.

Debt is demand-weighted over VITAL owned facts (IN_ANSWER + OWNED_NOT_SURFACED). CIs bootstrap over
QUESTIONS (nuggets cluster within a question); required question counts scale by bootstrap half-width
(cluster-consistent, R14 B3). Set C (low owned-fact denominator) suppresses its debt headline (R14 B4).
"""
from __future__ import annotations
import math
import random
from collections import Counter

_STAGES = ["ROUTER", "POOL", "RANK", "COMPOSE", "CONFIG", "UNRESOLVED"]
_MIN_DENOM = 20                    # B4: below this, no debt headline
_SC6_MAX = 0.30                    # R9: flag if oracle-incorrectness > 30%


def bootstrap_debt_ci(records, iters: int = 2000, rng=None) -> tuple:
    """95% percentile CI on debt, resampling QUESTIONS with replacement (cluster-robust)."""
    rng = rng or random.Random(0)
    by_q: dict = {}
    for r in records:
        if r.vital and r.fact_class in ("IN_ANSWER", "OWNED_NOT_SURFACED"):
            by_q.setdefault(r.question, []).append(r.fact_class == "OWNED_NOT_SURFACED")
    qs = list(by_q)
    if not qs:
        return (0.0, 0.0, 0.0)

    def _debt(sample_qs):
        num = sum(sum(by_q[q]) for q in sample_qs)
        den = sum(len(by_q[q]) for q in sample_qs)
        return num / den if den else 0.0

    point = _debt(qs)
    draws = sorted(_debt([rng.choice(qs) for _ in qs]) for _ in range(iters))
    lo, hi = draws[int(0.025 * iters)], draws[int(0.975 * iters)]
    return (point, lo, hi)


def required_n(p, facts_per_q, target_margin: float = 0.05, z: float = 1.96) -> dict | None:
    """Naive (fact-independence) sample size — reference only; the headline uses the cluster-consistent
    question count from questions_for_margin (B3)."""
    if facts_per_q <= 0:
        return None
    n_facts = (z * z * p * (1 - p)) / (target_margin * target_margin)
    return {"facts_needed": round(n_facts), "questions_needed": round(n_facts / facts_per_q)}


def questions_for_margin(half_width: float, n_questions_obs: int, target_margin: float) -> int:
    """Cluster-consistent (R14 B3): the bootstrap half-width scales as 1/sqrt(N_questions), so to reach
    `target_margin` you need N_obs * (half_width / target)^2 questions. Never fewer than what you have."""
    if target_margin <= 0 or half_width <= 0 or n_questions_obs <= 0:
        return n_questions_obs
    return max(n_questions_obs, math.ceil(n_questions_obs * (half_width / target_margin) ** 2))


def power_analysis(records, *, targets=(0.05, 0.10)) -> dict:
    """CI + required-question counts (overall and per stage) so the report is self-answering on scale."""
    point, lo, hi = bootstrap_debt_ci(records)
    half_width = (hi - lo) / 2.0
    owned_vital = [r for r in records if r.vital and r.fact_class in ("IN_ANSWER", "OWNED_NOT_SURFACED")]
    n_questions = len({r.question for r in owned_vital})
    denom = len(owned_vital)
    facts_per_q = (denom / n_questions) if n_questions else 0.0
    misses = [r for r in records if r.vital and r.fact_class == "OWNED_NOT_SURFACED"]
    total_miss = len(misses)
    stage_counts = Counter(r.stage for r in misses)

    tgt = {}
    for e in targets:
        tgt[f"{e}"] = {
            "questions_needed": questions_for_margin(half_width, n_questions, e),
            "facts_needed_naive": (required_n(point, facts_per_q, e) or {}).get("facts_needed"),
        }
    per_stage = {}
    for s in _STAGES:
        c = stage_counts.get(s, 0)
        if not c:
            continue
        share = c / total_miss if total_miss else 0.0
        # Agresti-Coull adjusted proportion for the variance term: avoids the p=0/p=1 collapse that
        # makes a stage owning ~all misses read as "~0 questions needed" (it has HIGH uncertainty).
        p_adj = (c + 2) / (total_miss + 4) if total_miss else 0.5
        rn = required_n(p_adj, facts_per_q, 0.10)     # ±10% on this stage's share of misses
        per_stage[s] = {"count": c, "share": share,
                        "questions_for_0.10": (rn or {}).get("questions_needed")}
    return {
        "debt_point": point, "ci_lo": lo, "ci_hi": hi, "half_width": half_width,
        "n_questions": n_questions, "n_owned_vital": denom, "facts_per_q": facts_per_q,
        "targets": tgt, "per_stage": per_stage,
        "ci_note": ("percentile bootstrap over %d questions; approximate at the tails for n≈50 "
                    "(use BCa if a tighter tail is needed)" % n_questions),
    }


def sc6_oracle_correctness(vital_records) -> dict:
    """Oracle-incorrectness rate = dropped / guarded (R9). A DROPPED_ORACLE fact already subsumes both
    'unsupported' and 'we_are_authority' — counted ONCE, with the split surfaced. Gate fails if > 30%."""
    n_guarded = len(vital_records)
    n_authority = sum(1 for r in vital_records if r.guard_verdict == "we_are_authority")
    n_unsupported = sum(1 for r in vital_records if r.guard_verdict == "unsupported")
    n_dropped = n_authority + n_unsupported
    rate = (n_dropped / n_guarded) if n_guarded else 0.0
    return {"n_guarded": n_guarded, "n_dropped": n_dropped, "n_authority": n_authority,
            "n_unsupported": n_unsupported, "rate": rate, "gate_pass": rate <= _SC6_MAX}


def debt_at_threshold(records, hi: float) -> float:
    """Sensitivity: recompute demand-weighted debt if the presence cut were `hi`, using each fact's
    stored max P(entail). Population = facts that went through the presence check (OWNED ∪ NOT_OWNED);
    a fact is 'owned' at hi iff its max_score >= hi. IN_ANSWER count is threshold-independent."""
    vital = [r for r in records if r.vital]
    n_in = sum(1 for r in vital if r.fact_class == "IN_ANSWER")
    pres_pop = [r for r in vital if r.fact_class in ("OWNED_NOT_SURFACED", "NOT_OWNED")]
    owned = sum(1 for r in pres_pop if r.max_score >= hi)
    denom = n_in + owned
    return (owned / denom) if denom else 0.0


def build_report(records, kappas: dict, *, set_name: str = "",
                 nugget_quality: dict | None = None, unsure_rates: dict | None = None) -> dict:
    vital = [r for r in records if r.vital]
    in_ans = [r for r in vital if r.fact_class == "IN_ANSWER"]
    owned_miss = [r for r in vital if r.fact_class == "OWNED_NOT_SURFACED"]
    not_owned = [r for r in vital if r.fact_class == "NOT_OWNED"]
    non_self = [r for r in vital if r.fact_class == "NON_SELF_CONTAINED"]
    low_conf = [r for r in not_owned if getattr(r.presence, "low_conf", False)]
    denom = len(in_ans) + len(owned_miss)
    debt = (len(owned_miss) / denom) if denom else 0.0
    sensitivity = {f"{hi}": debt_at_threshold(records, hi) for hi in (0.4, 0.5, 0.6)}
    stage_counts = Counter(r.stage for r in owned_miss)
    strat_counts = Counter(r.stratum for r in owned_miss)
    unresolved = stage_counts.get("UNRESOLVED", 0)
    attributed = len(owned_miss) - unresolved
    sc1 = all(k >= 0.6 for k in kappas.values()) if kappas else False
    sc5 = (attributed / len(owned_miss) >= 0.70) if owned_miss else False
    sc6 = sc6_oracle_correctness(vital)
    return {
        "set_name": set_name,
        "processing_debt": debt,
        "debt_reportable": denom >= _MIN_DENOM,        # B4
        "n_vital": len(vital), "n_in_answer": len(in_ans), "n_owned_miss": len(owned_miss),
        "n_not_owned": len(not_owned), "n_low_conf": len(low_conf),
        "n_non_self_contained": len(non_self),
        "debt_sensitivity": sensitivity,
        "denom": denom,
        "stage_counts": {s: stage_counts.get(s, 0) for s in _STAGES},
        "stratum_counts": dict(strat_counts),
        "kappas": kappas,
        "unsure_rates": unsure_rates or {},
        "nugget_quality": nugget_quality or {},
        "power": power_analysis(records),
        "sc6_rate": sc6["rate"], "sc6_detail": sc6,
        "SC1": sc1,                                    # judge trust gate
        "SC4": len(owned_miss) >= 5,                   # yield
        "SC5": sc5,                                    # attribution unambiguous >=70%
        "SC6": sc6["gate_pass"],                        # oracle-correctness gate
    }


def render_md(report: dict) -> str:
    name = f" — Set {report['set_name']}" if report.get("set_name") else ""
    p = report["power"]
    if report["debt_reportable"]:
        headline = (f"**Processing Debt (demand-weighted): {report['processing_debt']*100:.1f}%** "
                    f"(95% CI {p['ci_lo']*100:.0f}–{p['ci_hi']*100:.0f}%, cluster-bootstrap over "
                    f"{p['n_questions']} questions) — {report['n_owned_miss']} owned-misses / "
                    f"{report['denom']} vital owned facts")
    else:
        headline = (f"**Processing Debt: insufficient owned-fact denominator "
                    f"({report['denom']} < {_MIN_DENOM}) — not a debt estimate.**")
    lines = [f"# Processing-Debt Pilot Report{name}", "", headline, "",
             "> **Presence lean (2026-07-07):** headline counts CONFIDENT presence only "
             "(P(entail) ≥ HI via the NLI judge). This REVERSES the original design §3.2 "
             "generous-presence stance (chosen after Set A's granite+unsure→present lean inflated "
             "debt ~2×). Low-confidence [LO,HI) facts are surfaced as a separate bucket below, not "
             "counted in the headline — see the sensitivity band for how the number moves with HI.", "",
             "## Per-stage (owned-misses)", "", "| Stage | Count |", "|---|---|"]
    for s, c in report["stage_counts"].items():
        lines.append(f"| {s} | {c} |")

    lines += ["", "## Fact buckets", "",
              f"- IN_ANSWER: {report['n_in_answer']} · OWNED_NOT_SURFACED (debt): {report['n_owned_miss']} "
              f"· NOT_OWNED: {report['n_not_owned']}",
              f"- **Low-confidence presence** (NOT_OWNED with a span in [LO,HI); surfaced for "
              f"adjudication, excluded from headline): {report['n_low_conf']}",
              f"- **Non-self-contained** (dangling-anaphor nuggets excluded from the κ denominator "
              f"+ headline): {report['n_non_self_contained']}"]

    sens = report.get("debt_sensitivity", {})
    if sens:
        lines += ["", "## Threshold sensitivity (debt vs presence cut HI)", "",
                  "| HI | Debt |", "|---|---|"]
        for hi in ("0.4", "0.5", "0.6"):
            if hi in sens:
                lines.append(f"| {hi} | {sens[hi]*100:.1f}% |")

    lines += ["", "## Sample size (power analysis)", "",
              f"- Observed yield: {p['facts_per_q']:.2f} owned-vital facts/question over "
              f"{p['n_questions']} questions.", f"- CI note: {p['ci_note']}"]
    for e, d in p["targets"].items():
        lines.append(f"- For ±{float(e)*100:.0f}% overall: ~{d['questions_needed']} questions needed "
                     f"(cluster-consistent; naive fact count {d['facts_needed_naive']}).")
    for s, d in p["per_stage"].items():
        lines.append(f"  - {s}: {d['count']} misses ({d['share']*100:.0f}% of misses) → "
                     f"~{d['questions_for_0.10']} questions for ±10% on its share.")

    lines += ["", "## Instrument validity (Cohen's κ)", ""]
    ur = report.get("unsure_rates", {})
    for k, v in report["kappas"].items():
        extra = f" · unsure-rate {ur[k]:.2f}" if k in ur else ""
        lines.append(f"- {k}: κ={v:.3f}{extra}")

    nq = report.get("nugget_quality") or {}
    if nq:
        lines += ["", "## Decompose quality (nugget set)", "",
                  f"- precision={nq.get('precision', 0):.2f} "
                  f"(accepted {nq.get('accepted')}/{nq.get('total_machine')}), "
                  f"recall={nq.get('recall', 0):.2f} (added {nq.get('added')})"]

    s6 = report["sc6_detail"]
    lines += ["", "## Success criteria",
              f"- SC1 (κ≥0.6 both decisions): {'PASS' if report['SC1'] else 'FAIL'}",
              f"- SC4 (≥5 owned-misses): {'PASS' if report['SC4'] else 'FAIL'}",
              f"- SC5 (≥70% attributed): {'PASS' if report['SC5'] else 'FAIL'}",
              f"- SC6 (oracle-incorrectness ≤30%): {'PASS' if report['SC6'] else 'FAIL'} "
              f"(rate {s6['rate']*100:.0f}% = {s6['n_unsupported']} unsupported + "
              f"{s6['n_authority']} we-are-authority / {s6['n_guarded']} guarded)"]
    return "\n".join(lines)


def compare_sets(reports: dict) -> str:
    """Combined per-set comparison. Low-denominator sets (B4) are asterisked, not shown as a debt number."""
    lines = ["# Per-set comparison", "",
             "| Set | Debt | 95% CI | Owned-vital | κ(in_answer) | κ(presence) |",
             "|---|---|---|---|---|---|"]
    footnote = False
    for name, rep in reports.items():
        p = rep["power"]
        ka = rep["kappas"].get("in_answer")
        kp = rep["kappas"].get("presence")
        ka_s = f"{ka:.2f}" if ka is not None else "—"
        kp_s = f"{kp:.2f}" if kp is not None else "—"
        if rep["debt_reportable"]:
            debt_s = f"{rep['processing_debt']*100:.1f}%"
            ci_s = f"{p['ci_lo']*100:.0f}–{p['ci_hi']*100:.0f}%"
        else:
            debt_s = "n/a*"
            ci_s = "—"
            footnote = True
        lines.append(f"| {name} | {debt_s} | {ci_s} | {rep['denom']} | {ka_s} | {kp_s} |")
    if footnote:
        lines += ["", "\\* insufficient owned-fact denominator (<20) — not a debt estimate (R14 B4)."]
    return "\n".join(lines)
