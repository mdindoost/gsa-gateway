#!/usr/bin/env python
"""Aggregate eval/results_judged.jsonl -> coverage + accuracy + the gap list (the targeted
to-do). Driven by scripts/eval.sh.

Accuracy GATE (backlog #4): pass --min-answered / --min-correct (percent floors) to turn the
report into a pass/fail gate — prints PASS/FAIL and exits non-zero on a regression. Default
(no thresholds) is report-only (always exit 0). eval_run.py owns --limit; both scripts tolerate
each other's flags (parse_known_args)."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def gate_result(answered_pct: float, correct_pct: float,
                min_answered: float | None, min_correct: float | None) -> tuple[bool, list[str]]:
    """Pure gate decision. Returns (passed, lines). No thresholds → (True, []) (report-only)."""
    lines: list[str] = []
    passed = True
    if min_answered is not None:
        ok = answered_pct >= min_answered
        passed = passed and ok
        lines.append(f"{'PASS' if ok else 'FAIL'} answered {answered_pct:.0f}% (floor {min_answered:.0f}%)")
    if min_correct is not None:
        ok = correct_pct >= min_correct
        passed = passed and ok
        lines.append(f"{'PASS' if ok else 'FAIL'} correct {correct_pct:.0f}% (floor {min_correct:.0f}%)")
    return passed, lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-answered", type=float, default=None, help="gate: min %% answered (kb+live)")
    ap.add_argument("--min-correct", type=float, default=None, help="gate: min %% correct of answered")
    args, _ = ap.parse_known_args()   # tolerate eval_run's flags (e.g. --limit)

    recs = [json.loads(l) for l in open(REPO / "eval" / "results_judged.jsonl", encoding="utf-8")]
    n = len(recs)
    cov = Counter(r["class"] for r in recs)
    answered = [r for r in recs if r["class"] in ("kb", "live")]
    acc = Counter(r.get("judge") for r in answered)
    na = len(answered)
    answered_pct = 100 * (cov["kb"] + cov["live"]) / n if n else 0
    correct_pct = 100 * acc["correct"] / na if na else 0

    print(f"\n=== EVAL REPORT — {n} questions ===")
    print(f"COVERAGE: {cov['kb']} KB · {cov['live']} live · {cov['deflect']} deflected · "
          f"{cov.get('error', 0)} error   ({answered_pct:.0f}% answered)")
    if na:
        print(f"ACCURACY (of {na} answered): {acc['correct']} correct · {acc['partial']} partial · "
              f"{acc['wrong']} wrong   ({correct_pct:.0f}% correct)")

    bycat: dict[str, Counter] = {}
    for r in recs:
        bycat.setdefault(r["cat"], Counter())[r["class"]] += 1
    print("\nBY CATEGORY (kb/live/deflect):")
    for cat, c in sorted(bycat.items()):
        print(f"  {cat:24} {c['kb']:>3} / {c['live']:>3} / {c['deflect']:>3}")

    print("\n=== GAPS (deflected or judged wrong — the targeted to-do) ===")
    gaps = [r for r in recs if r["class"] == "deflect" or r.get("judge") == "wrong"]
    for r in gaps:
        print(f"  [{r['class']}/{r.get('judge', '-')}] {r['q']}")
    if not gaps:
        print("  (none)")

    # ── Accuracy gate (opt-in) ────────────────────────────────────────────────
    passed, gate_lines = gate_result(answered_pct, correct_pct, args.min_answered, args.min_correct)
    if gate_lines:
        print("\n=== GATE ===")
        for line in gate_lines:
            print(f"  {line}")
        print(f"  → {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
