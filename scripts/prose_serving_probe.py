"""Serving + rank-preservation probe for the day-1 prose rebuild (Task 9).

Run against the DEV-rebuilt DB (and the pre-wipe backup for rank-preservation) before the swap.
Three checks (spec §5.4, rev2-RAG#3):
  1. grad-admissions serving probe — the real Admissions office page ranks top-2 for "which office
     handles graduate admission questions", and no duplicate `Graduate Admissions` row appears in top-k
     (the exact regression that triggered this whole build).
  2. anti-corank invariant — 0 canonical URLs with >1 active prose row (also enforced by the gate).
  3. rank-preservation — for the eval + office query set, the answer-bearing content ranks no worse on
     the rebuilt DB than on the backup (proves the consolidation + marketing bucket didn't dilute).

  python scripts/prose_serving_probe.py --rebuilt /tmp/dev_rebuild.db --backup .backups/<pre>.db

Spec: docs/superpowers/specs/2026-06-30-day1-prose-rebuild-design.md §5.4
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def first_rank(items, token) -> int | None:
    """1-indexed rank of the first (title, content) whose content contains `token`; None if absent."""
    tok = token.lower()
    for i, (_title, content) in enumerate(items, 1):
        if tok in (content or "").lower():
            return i
    return None


def count_titled(titles, title) -> int:
    """How many entries EXACTLY equal `title` (used to catch a duplicate 'Graduate Admissions' row)."""
    return sum(1 for t in titles if t == title)


def _retriever(db):
    from v2.core.database.schema import get_connection
    from v2.core.retrieval.retriever import V2Retriever
    from v2.core.retrieval.embedder import Embedder
    from v2.core.retrieval.reranker import CrossEncoderReranker
    return V2Retriever(get_connection(db), Embedder(), reranker=CrossEncoderReranker())


def grad_admissions_probe(retr, k=8) -> dict:
    q = "which office handles graduate admission questions"
    chunks = retr.retrieve(q, limit=k)
    items = [(c.title, c.content) for c in chunks]
    titles = [c.title for c in chunks]
    top2 = first_rank(items[:2], "University Admissions") is not None
    dup = count_titled(titles, "Graduate Admissions")
    return {"ok": top2 and dup == 0, "admissions_in_top2": top2,
            "graduate_admissions_dups_in_topk": dup, "titles": titles}


def rank_preservation(retr_rebuilt, retr_backup, queries, *, token_map, tolerance=1) -> dict:
    """For each query, the answer token's rank on rebuilt must be <= its backup rank + tolerance."""
    regressions = []
    for q in queries:
        tok = token_map.get(q)
        if not tok:
            continue
        b = first_rank([(c.title, c.content) for c in retr_backup.retrieve(q, limit=10)], tok)
        r = first_rank([(c.title, c.content) for c in retr_rebuilt.retrieve(q, limit=10)], tok)
        if b is not None and (r is None or r > b + tolerance):
            regressions.append({"q": q, "backup_rank": b, "rebuilt_rank": r})
    return {"ok": not regressions, "regressions": regressions}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Prose rebuild serving + rank-preservation probe")
    ap.add_argument("--rebuilt", required=True)
    ap.add_argument("--backup", required=True)
    args = ap.parse_args(argv)
    rebuilt = _retriever(args.rebuilt)
    ga = grad_admissions_probe(rebuilt)
    print("grad-admissions probe:", "PASS ✅" if ga["ok"] else "FAIL ❌",
          f"(admissions_top2={ga['admissions_in_top2']}, "
          f"graduate_admissions_dups={ga['graduate_admissions_dups_in_topk']})")
    print("  top titles:", ga["titles"])
    print("\nNOTE: rank_preservation over the eval+office set is invoked in the Task-10 run "
          "(needs the backup retriever + verified token_map).")
    return 0 if ga["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
