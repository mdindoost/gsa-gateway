#!/usr/bin/env python
"""Score the abstain set: what fraction of genuinely-unanswerable questions the bot correctly
DEFLECTS rather than confabulates (spec reject-criterion #3). Reads an abstain results.jsonl
produced by:  python scripts/eval_run.py --questions eval/abstain_questions.txt --out eval/abstain_results.jsonl
(run with LIVE_ENABLED=0 to measure the gate itself, not the live-search fallback).
Usage: python scripts/eval_abstain.py [eval/abstain_results.jsonl]"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def score_abstain(recs: list[dict]) -> dict:
    total = len(recs)
    leaks = [{"q": r.get("q"), "class": r.get("class")} for r in recs if r.get("class") != "deflect"]
    deflected = total - len(leaks)
    return {
        "total": total,
        "deflected": deflected,
        "answered": len(leaks),
        "rate": deflected / total if total else 0.0,
        "leaks": leaks,
    }


def _main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "eval" / "abstain_results.jsonl"
    with open(src, encoding="utf-8") as f:
        recs = [json.loads(l) for l in f]
    s = score_abstain(recs)
    print(f"abstain-correctness: {s['deflected']}/{s['total']} deflected = {100*s['rate']:.1f}%")
    if s["leaks"]:
        print(f"LEAKS — answered an unanswerable question ({s['answered']}):")
        for lk in s["leaks"]:
            print(f"  [{lk['class']}] {lk['q']}")


if __name__ == "__main__":
    _main()
