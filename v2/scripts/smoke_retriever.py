"""Step 4 verification — run the Step 3 smoke queries through the hybrid retriever.

Shows, per result, which leg surfaced it (semantic / keyword / hybrid) so the RRF
effect is visible — especially Test 2 (the VP Finances contact that vector-only
search missed).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from v2.core.database.schema import get_connection
from v2.core.retrieval.embedder import Embedder
from v2.core.retrieval.retriever import V2Retriever

GREEN, YELLOW, DIM, RESET = "\033[92m", "\033[93m", "\033[2m", "\033[0m"

TESTS = [
    ("Conference funding", "how do I get money for conference", 3),
    ("GSA finances contact", "who is in charge of GSA finances", 3),
    ("MMI workshop", "workshop on multimedia research", 3),
    ("Cross domain funding+workshop", "funding to attend the workshop", 5),
    ("Club budget violations", "club budget violations", 3),
]


def main():
    db = sys.argv[1] if len(sys.argv) > 1 else str(REPO_ROOT / "gsa_gateway.db")
    conn = get_connection(db)
    r = V2Retriever(conn, Embedder())

    bar = "═" * 60
    print(bar + "\n  V2 Retriever — Hybrid RRF (vec + FTS5)\n" + bar)
    for i, (name, query, k) in enumerate(TESTS, 1):
        print(f"\nTest {i} — {name}:  (query: \"{query}\")")
        for c in r.retrieve(query, limit=k):
            org = c.org_path.split(" > ")[-1]
            sim = f"{c.similarity:.3f}" if c.similarity is not None else "  -  "
            tag = {"hybrid": GREEN + "hybrid" + RESET,
                   "keyword": YELLOW + "keyword" + RESET,
                   "semantic": "semantic"}[c.source]
            print(f"  → [{org}/{c.type}] {c.title}")
            print(f"       {DIM}source={tag}{DIM}  sim={sim}  rrf={c.rrf_score:.4f}{RESET}")

    # Targeted check: does the VP Finances contact now appear for the Test-2 query?
    print("\n" + bar)
    hits = r.retrieve("who is in charge of GSA finances", limit=5)
    vpf = next((c for c in hits if c.type == "contact" and "Finance" in (c.title or "")), None)
    if vpf:
        pos = [c.item_id for c in hits].index(vpf.item_id) + 1
        print(f"  {GREEN}✓ VP Finances contact now retrieved at rank {pos} "
              f"(source={vpf.source}){RESET}  — RRF fixed the Test-2 miss")
    else:
        print(f"  {YELLOW}⚠ VP Finances contact still not in top 5{RESET}")
    print(bar)
    conn.close()


if __name__ == "__main__":
    main()
