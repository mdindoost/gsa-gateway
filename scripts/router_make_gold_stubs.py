"""Generate BLIND gold-label stubs for the router test set.

Harvests distinct real user questions NOT already in the dataset and writes family:"?" stubs to
eval/router/gold_stubs.jsonl. You then fill in `family` (+ `skill`/`source`/`slots`) for each row
WITHOUT looking at any LLM proposal — that independence is what makes the test set trustworthy.

Usage: python scripts/router_make_gold_stubs.py [N]   (default 60)
After labeling, merge with merge_blind_labels (see eval/router/LABELING_PROTOCOL.md).
"""
from __future__ import annotations
import sys, json
from pathlib import Path
from scripts.router_harvest_queries import harvest
from v2.eval.router.dataset import load_dataset
from v2.eval.router.labeling import make_blind_stubs

if __name__ == "__main__":
    data = "eval/router/labeled_routes.jsonl"
    db = "gsa_gateway.db"
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    existing = {r.query.strip().lower() for r in load_dataset(data)}
    qs = [q for q in harvest(db, limit=2000) if q.strip().lower() not in existing][:n]
    stubs = make_blind_stubs(qs, start_id=0, split="test")
    out = Path("eval/router/gold_stubs.jsonl")
    out.write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in stubs) + "\n")
    print(f"wrote {len(stubs)} blind stubs -> {out}")
    print("Label each row's 'family' (+ skill/source/slots) WITHOUT consulting any proposed route.")
