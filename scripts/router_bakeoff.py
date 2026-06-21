"""Phase-0 router bake-off CLI: load labeled set -> split -> run arms -> gate -> report.

Usage: python scripts/router_bakeoff.py [labeled_routes.jsonl] [gsa_gateway.db]
Needs Ollama up (uses the real nomic encoder). Builds a slot-masker from the live KG and reports
BOTH the entity-disjoint split (primary honesty metric) and the paraphrase-disjoint split.
Writes eval/router/bakeoff_report.md.
"""
from __future__ import annotations
import sqlite3, sys
from pathlib import Path
from v2.eval.router.dataset import load_dataset
from v2.eval.router.encode import real_encoder
from v2.eval.router.mask import build_masker_from_db
from v2.eval.router.bakeoff import run_bakeoff, format_report

if __name__ == "__main__":
    data = sys.argv[1] if len(sys.argv) > 1 else "eval/router/labeled_routes.jsonl"
    db = sys.argv[2] if len(sys.argv) > 2 else "gsa_gateway.db"
    examples = load_dataset(data)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    masker = build_masker_from_db(conn)
    sections = []
    for mode, label in (("entity", "entity-disjoint (PRIMARY)"), ("paraphrase", "paraphrase-disjoint")):
        result = run_bakeoff(examples, conn, real_encoder, masker=masker,
                             split_mode=mode, val_frac=0.2)
        sections.append(format_report(result, title=f"Phase-0 Bake-off — {label} split"))
    report = "\n\n---\n\n".join(sections)
    Path("eval/router/bakeoff_report.md").write_text(report)
    print(report)
