from __future__ import annotations
import json
from collections import Counter

# subsystem each failure class points at — the "where to go fix it" hint
_WHERE = {
    "fabrication": "retrieval/generation grounding",
    "routing_failure": "the router (rules / slot-extractor)",
    "resolution_failure": "the fuzzy resolver (WS2)",
}

def _expected_gist(r: dict) -> str:
    try:
        e = json.loads(r.get("expected_json") or "{}")
    except Exception:
        return "?"
    val = e.get("value") or e.get("members") or e.get("missing_field") or e.get("type")
    return str(val)[:90]

def _fail_detail(r: dict) -> list[str]:
    arm = r.get("arm") or "?"
    variant = f"/{r['variant_type']}" if r.get("variant_type") else ""
    route = f"{r.get('family') or '?'}:{r.get('skill') or '-'}"
    ans = (r.get("answer_text") or "").replace("\n", " ")[:160]
    return [
        f"- [{r.get('failure_class')}] {r.get('item_key')} · {arm}{variant} · routed → {route}",
        f"    Q: {r.get('question_text')}",
        f"    expected: {_expected_gist(r)}",
        f"    Kavosh said: {ans}",
    ]

def build_report(rows: list[dict], prev_rows: list[dict] | None = None) -> str:
    total = len(rows)
    passed = sum(1 for r in rows if r["result"] == "pass")
    fails = [r for r in rows if r["result"] == "fail"]
    classes = Counter(r["failure_class"] for r in fails if r["failure_class"])
    data_gaps = [r for r in rows if r.get("data_gap")]
    fabrications = [r for r in fails if r["failure_class"] == "fabrication"]
    errored = sum(1 for r in rows if r["result"] == "error")

    L = []
    L.append("# Kavosh Auto-Eval — Triage Report\n")
    L.append(f"Total questions: {total}   Pass: {passed} ({100*passed/total:.1f}%)\n" if total else "No questions.\n")
    L.append("## Failure classes (separate; fabrication first)")
    L.append(f"- 🔴 fabrication: {classes.get('fabrication', 0)}")
    L.append(f"- resolution_failure: {classes.get('resolution_failure', 0)}")
    L.append(f"- routing_failure: {classes.get('routing_failure', 0)}")
    L.append(f"- data_gap (data problem, NOT a Kavosh bug): {len(data_gaps)}")
    L.append(f"- errored (harness/transport failures, excluded from pass/fail): {errored}\n")

    L.append("## 🔴 Fabrications (full list — zero tolerance)")
    if not fabrications:
        L.append("- none\n")
    for r in fabrications:
        L.append(f"- [{r['item_key']}] Q: {r['question_text']}\n    A: {r['answer_text'][:200]}")
    L.append("")

    # Full per-failure detail — WHICH question failed and WHERE (the core deliverable).
    # Ordered by severity so the most important failures are read first. Not truncated: every
    # failing question is listed so nothing hides behind an aggregate count.
    L.append("## Failure details — which question failed & where")
    if not fails:
        L.append("- none 🎉\n")
    else:
        L.append(f"_Fix-location by class: " +
                 ", ".join(f"{k} → {v}" for k, v in _WHERE.items()) + "._\n")
        order = {"fabrication": 0, "routing_failure": 1, "resolution_failure": 2}
        for r in sorted(fails, key=lambda x: (order.get(x.get("failure_class"), 9), x.get("item_key") or "")):
            L.extend(_fail_detail(r))
        L.append("")

    # Top failing items (concentration view)
    item_fails = Counter(r["item_key"] for r in fails)
    L.append("## Top failing items (concentration)")
    for key, c in item_fails.most_common(15):
        L.append(f"- {key}: {c} failures")
    L.append("")

    # Resolution failures by variant_type
    res = [r for r in fails if r["failure_class"] == "resolution_failure"]
    vt = Counter(r.get("variant_type") for r in res)
    L.append("## Resolution failures by variant_type (WS2 tuning surface)")
    for v, c in vt.most_common():
        L.append(f"- {v}: {c}")
    L.append("")

    # Data-gap report (separate)
    L.append("## Data-gap report (route to crawler backlog — NOT routing bugs)")
    dg = Counter(r["item_key"] for r in data_gaps)
    for key, c in dg.most_common(30):
        L.append(f"- {key}: {c} missing-field questions correctly abstained")
    L.append("")

    # Regression delta
    if prev_rows is not None and prev_rows:
        p_total = len(prev_rows); p_pass = sum(1 for r in prev_rows if r["result"] == "pass")
        p_fab = sum(1 for r in prev_rows if r["failure_class"] == "fabrication")
        cur_rate = 100*passed/total if total else 0
        prev_rate = 100*p_pass/p_total if p_total else 0
        L.append("## Regression delta (vs previous run at same commit)")
        L.append(f"- pass rate: {prev_rate:.1f}% → {cur_rate:.1f}%  (Δ {cur_rate-prev_rate:+.1f})")
        L.append(f"- fabrications: {p_fab} → {len(fabrications)}  (Δ {len(fabrications)-p_fab:+d})")
    return "\n".join(L)
