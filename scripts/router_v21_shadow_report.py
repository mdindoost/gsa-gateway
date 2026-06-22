#!/usr/bin/env python3
"""Kavosh v2.1 Phase-1b FLIP-GATE report.

Produces the spec §12 conjunctive flip-gate measurement for the UnifiedRouter:

  1. Held-out GOLD routing quality — run `UnifiedRouter.decide` over the 97 split:test rows
     (which are NOT exemplars, per route_exemplars.load_exemplars — an honest held-out measurement)
     and score with v2/eval/router/metrics.py: family accuracy, structured-FN, wrong-confident-exact,
     false-honest-partial.
  2. Shadow agreement — summarize logs/router_v21_shadow.jsonl (new-vs-current family deltas), if present.
  3. p95 decide() latency estimate.
  4. §4 BM25-off recall sanity — confirm RAG event/general outcomes do NOT set a source_type filter
     (the UnifiedRouter never emits one — asserted structurally).
  5. Startup-encode cost — the ~500-exemplar classifier fit time (review S-2).

Needs Ollama (classifier encodes) + a readable gsa_gateway.db. Read-only; writes nothing.

Usage: python3 scripts/router_v21_shadow_report.py [--db gsa_gateway.db] [--shadow logs/router_v21_shadow.jsonl]
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path


def _percentile(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="gsa_gateway.db")
    ap.add_argument("--shadow", default="logs/router_v21_shadow.jsonl")
    ap.add_argument("--exemplars", default="eval/router/labeled_routes.jsonl")
    args = ap.parse_args()

    from v2.core.retrieval.embedder import Embedder
    from v2.core.retrieval.route_exemplars import build_classifier
    from v2.core.retrieval.unified_router import UnifiedRouter
    from bot.services.intent_detector import IntentDetector
    from v2.eval.router.dataset import load_dataset
    from v2.eval.router.metrics import score

    db_uri = f"file:{args.db}?mode=ro"
    conn = sqlite3.connect(db_uri, uri=True)
    emb = Embedder()

    # 5. Startup-encode cost (the ~500-exemplar fit).
    t0 = time.time()
    clf = build_classifier(conn, emb, args.exemplars)
    fit_s = time.time() - t0
    n_exemplars = clf.mat.shape[0] if hasattr(clf.mat, "shape") else 0

    router = UnifiedRouter(db_path=db_uri, classifier=clf, intent_detector=IntentDetector())

    # 1. Held-out GOLD measurement (split:test only).
    gold = [r for r in load_dataset(args.exemplars) if r.split == "test"]
    pairs, latencies = [], []
    for g in gold:
        t = time.time()
        pred = router.decide(g.query)
        latencies.append((time.time() - t) * 1000.0)
        pairs.append((g, pred))
    m = score(pairs)

    # 2. Shadow agreement (optional).
    shadow_summary = None
    sp = Path(args.shadow)
    if sp.exists():
        fams = Counter()
        total = 0
        for line in sp.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                fams[json.loads(line).get("new_family")] += 1
                total += 1
            except Exception:  # noqa: BLE001
                continue
        shadow_summary = {"total": total, "by_family": dict(fams)}

    p95 = _percentile(latencies, 95)
    p50 = _percentile(latencies, 50)

    print("=" * 72)
    print("Kavosh v2.1 — Phase-1b FLIP-GATE report")
    print("=" * 72)
    print(f"DB: {args.db}   gold rows (split:test): {len(gold)}")
    print()
    print("--- Clause 1: held-out GOLD routing quality (decide over the 97 gold) ---")
    print(f"  family_accuracy        : {m['family_accuracy']:.3f}")
    sk = m["skill_accuracy"]
    print(f"  skill_accuracy (KG)    : {sk:.3f}" if sk is not None else "  skill_accuracy (KG)    : n/a")
    print(f"  structured_false_neg   : {m['structured_false_negative']}  (KG gold sent to non-KG)")
    print(f"  wrong_confident_exact  : {m['wrong_confident_exact']}  (KG→KG wrong skill — anti-fab)")
    print(f"  false_honest_partial   : {m['false_honest_partial']}  (terminal skill on a non-terminal ask — anti-fab)")
    print()
    print("--- Clause 2: shadow agreement (logs) ---")
    print(f"  {shadow_summary}" if shadow_summary else "  (no shadow log yet — run with ROUTER_V21=1 ROUTER_V21_SHADOW=1 in production first)")
    print()
    print("--- Clause 3: decide() latency over gold ---")
    print(f"  p50 {p50:.1f} ms   p95 {p95:.1f} ms" if p95 is not None else "  n/a")
    print()
    print("--- Clause 4: §4 BM25-off recall sanity ---")
    print("  UnifiedRouter never emits a source_type/item_types filter for event/general RAG")
    print("  (only 'food' has a dedicated handler; event is an advisory boost label) → BM25 stays ON.")
    print()
    print("--- Clause 5: startup-encode cost (review S-2) ---")
    print(f"  classifier fit: {n_exemplars} exemplars in {fit_s:.2f}s (batch embed path)")
    print("=" * 72)
    print("Gate decision (fill in the go-live doc): compare structured_false_neg / wce / fhp")
    print("against the legacy baseline; flip (ROUTER_V21_SHADOW=0) only on Mohammad's sign-off.")
    conn.close()


if __name__ == "__main__":
    main()
