#!/usr/bin/env python
"""Aggregate eval/results_judged.jsonl -> coverage + accuracy + the gap list (the targeted
to-do). Driven by scripts/eval.sh."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
recs = [json.loads(l) for l in open(REPO / "eval" / "results_judged.jsonl", encoding="utf-8")]
n = len(recs)
cov = Counter(r["class"] for r in recs)
answered = [r for r in recs if r["class"] in ("kb", "live")]
acc = Counter(r.get("judge") for r in answered)
na = len(answered)

print(f"\n=== EVAL REPORT — {n} questions ===")
print(f"COVERAGE: {cov['kb']} KB · {cov['live']} live · {cov['deflect']} deflected · "
      f"{cov.get('error', 0)} error   ({100*(cov['kb']+cov['live'])//n if n else 0}% answered)")
if na:
    print(f"ACCURACY (of {na} answered): {acc['correct']} correct · {acc['partial']} partial · "
          f"{acc['wrong']} wrong   ({100*acc['correct']//na}% correct)")

# per-category coverage
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
