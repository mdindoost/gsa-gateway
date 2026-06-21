"""Phase-0 router bake-off CLI: load labeled set → split → run 3 arms → gate → report.

Usage: python scripts/router_bakeoff.py [labeled_routes.jsonl] [gsa_gateway.db]
Needs Ollama up (uses the real nomic encoder). Writes eval/router/bakeoff_report.md.
"""
from __future__ import annotations
import sqlite3, sys
from pathlib import Path
from v2.eval.router.dataset import load_dataset
from v2.eval.router.encode import real_encoder
from v2.eval.router.bakeoff import run_bakeoff, format_report

if __name__ == "__main__":
    data = sys.argv[1] if len(sys.argv) > 1 else "eval/router/labeled_routes.jsonl"
    db = sys.argv[2] if len(sys.argv) > 2 else "gsa_gateway.db"
    examples = load_dataset(data)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    result = run_bakeoff(examples, conn, real_encoder)          # uses Ollama via real_encoder
    report = format_report(result)
    Path("eval/router/bakeoff_report.md").write_text(report)
    print(report)
