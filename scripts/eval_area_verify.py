#!/usr/bin/env python3
"""Precision/recall gate for the LLM area-verify step (spec R7).

Scores `area_expand.llm_verify` against a labeled gold set of (query, tag, belongs) pairs —
including the measured false-friend traps (neural↔computer networks, service-learning, the
systems-head fanout, directional parent-field rejection). Ships the AREA_EXPAND flag ON only
when PRECISION >= the gate (default 0.9); must be re-run on any AREA_VERIFY_MODEL swap.

Usage: AREA_VERIFY_MODEL=llama3.1:8b python3 scripts/eval_area_verify.py [--gate 0.9]
Exit 0 iff precision >= gate; else exit 1.
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from v2.core.retrieval import area_expand

GOLD = Path(__file__).resolve().parent.parent / "eval" / "area_expand" / "gold_pairs.jsonl"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", type=float, default=0.9)
    args = ap.parse_args()

    pairs = [json.loads(l) for l in GOLD.read_text().splitlines() if l.strip()]

    def score(predict) -> tuple[float, float, list[str]]:
        """predict(query) -> set of tags the model accepted for that query. Returns (precision, recall, wrong)."""
        tp = fp = fn = 0
        wrong: list[str] = []
        by_q: dict[str, set[str]] = {}
        for p in pairs:
            by_q.setdefault(p["query"], predict(p["query"]))
            accepted = p["tag"] in by_q[p["query"]]
            if accepted and p["belongs"]:
                tp += 1
            elif accepted and not p["belongs"]:
                fp += 1; wrong.append(f"  FALSE POSITIVE: {p['query']!r} <- {p['tag']!r}")
            elif not accepted and p["belongs"]:
                fn += 1; wrong.append(f"  FALSE NEGATIVE: {p['query']!r} <- {p['tag']!r}")
        prec = tp / (tp + fp) if (tp + fp) else 1.0
        rec = tp / (tp + fn) if (tp + fn) else 1.0
        return prec, rec, wrong

    model = os.getenv("AREA_VERIFY_MODEL", "granite4:tiny-h")
    tags_by_q: dict[str, list[str]] = {}
    for p in pairs:
        tags_by_q.setdefault(p["query"], []).append(p["tag"])

    # (1) per-pair (chunk-of-1) — isolation precision: verify each tag on its own.
    def predict_isolated(q: str) -> set[str]:
        out: set[str] = set()
        for t in tags_by_q[q]:
            out.update(area_expand.llm_verify(q, [t]))
        return out

    # (2) BATCHED (production shape): all of a query's tags in ONE call → llm_verify chunks at AREA_VERIFY_CHUNK.
    def predict_batched(q: str) -> set[str]:
        return set(area_expand.llm_verify(q, tags_by_q[q]))

    p1, r1, _ = score(predict_isolated)
    p2, r2, wrong2 = score(predict_batched)

    print(f"model={model}  queries={len(tags_by_q)}  pairs={len(pairs)}  (gate: precision>={args.gate})")
    print(f"  per-pair  (chunk-of-1)  PRECISION={p1:.3f} RECALL={r1:.3f}")
    print(f"  BATCHED   (prod shape)  PRECISION={p2:.3f} RECALL={r2:.3f}   <- the authoritative gate")
    if wrong2:
        print("batched misclassifications:")
        print("\n".join(wrong2))
    ok = p2 >= args.gate            # gate on the PRODUCTION (batched) shape, not the easy chunk-of-1
    print("GATE: PASS" if ok else "GATE: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
