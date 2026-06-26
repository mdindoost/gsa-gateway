#!/usr/bin/env python
"""Quantify the local auto-judge's run-to-run variance so eval A/B claims must exceed judge noise.
Re-judges an existing eval/results.jsonl N times (the answers are fixed; only the judge re-runs)
and reports mean/stdev/min/max of the aggregate correct%. Usage: python scripts/eval_judge_variance.py [N]"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.eval_judge import judge_record


async def variance_runs(recs, oc, n: int) -> list[float]:
    fractions = []
    for _ in range(n):
        correct = answered = 0
        for r in recs:
            verdict = await judge_record(oc, r)
            if verdict in ("deflect", "error"):
                continue
            answered += 1
            if verdict == "correct":
                correct += 1
        fractions.append(correct / answered if answered else 0.0)
    return fractions


def summarize(fractions: list[float]) -> dict:
    return {
        "runs": len(fractions),
        "mean": statistics.fmean(fractions) if fractions else 0.0,
        "stdev": statistics.stdev(fractions) if len(fractions) > 1 else 0.0,
        "min": min(fractions) if fractions else 0.0,
        "max": max(fractions) if fractions else 0.0,
    }


async def _main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    src = REPO / "eval" / "results.jsonl"
    recs = [json.loads(l) for l in open(src, encoding="utf-8")]
    from bot.services.ollama_client import OllamaClient
    fr = await variance_runs(recs, OllamaClient(), n)
    s = summarize(fr)
    print(f"judge variance over {s['runs']} runs: "
          f"correct% mean={100*s['mean']:.1f} stdev={100*s['stdev']:.2f} "
          f"min={100*s['min']:.1f} max={100*s['max']:.1f}")
    print(f"→ a claimed A/B win should exceed ~{100*2*s['stdev']:.1f} pts (2σ) to beat judge noise.")


if __name__ == "__main__":
    asyncio.run(_main())
