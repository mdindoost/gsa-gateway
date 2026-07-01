"""Qwen3-Embedding prefix-wiring DRY RUN (owner's gate before the full corpus embed).

Proves the asymmetric prefix is wired correctly BEFORE we pay for the whole pass:
  * pull N real prose docs from a DB, embed each as a PASSAGE (raw, no prefix),
  * for a handful of target docs, embed a natural query TWO ways —
      WITH the Instruct prefix (embed_query) and WITHOUT it (embed_document / raw) —
  * rank the target doc under each, and show the hit quality side-by-side.

If the prefix is wired + helping, the WITH-prefix query ranks its target at/above the
raw query. Uses the SAME Embedder code path the pipeline uses (not a re-implementation),
so a green dry run also validates the ingestion/query wiring itself.

  EMBEDDING_MODEL=qwen3-embedding:0.6b python scripts/qwen_dryrun.py --db /path/to.db --docs 10

Spec: owner request 2026-06-30 (Qwen switch) — 10-doc dry run, with/without query prefix.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def rank_of(query_vec, doc_vecs, target_idx: int) -> int:
    """1-indexed rank of doc[target_idx] when docs are sorted by descending dot product
    with query_vec (vectors are L2-normalized, so dot == cosine). Ties count as better."""
    tscore = sum(q * d for q, d in zip(query_vec, doc_vecs[target_idx]))
    better = 0
    for i, dv in enumerate(doc_vecs):
        if i == target_idx:
            continue
        if sum(q * d for q, d in zip(query_vec, dv)) > tscore:
            better += 1
    return better + 1


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    top1_with = sum(1 for r in rows if r["rank_with"] == 1)
    top1_without = sum(1 for r in rows if r["rank_without"] == 1)
    mean_with = sum(r["rank_with"] for r in rows) / n if n else 0.0
    mean_without = sum(r["rank_without"] for r in rows) / n if n else 0.0
    return {"n": n, "top1_with": top1_with, "top1_without": top1_without,
            "mean_rank_with": mean_with, "mean_rank_without": mean_without}


def _load_docs(db: str, n: int) -> list[dict]:
    """Pull N DIVERSE prose docs: distinct, meaningful titles (one row per title) spread
    across orgs — so target queries are distinguishable and retrieval is discriminating.
    Excludes the near-identical 'Personal website' faculty webpage dumps."""
    from v2.core.database.schema import get_connection
    conn = get_connection(db)
    rows = conn.execute(
        "SELECT id, title, content FROM knowledge_items WHERE id IN ("
        "  SELECT MIN(id) FROM knowledge_items "
        "  WHERE is_active=1 AND type='policy' "
        "  AND length(content) BETWEEN 400 AND 8000 "
        "  AND length(title) > 12 AND title NOT LIKE '%Personal website%' "
        "  GROUP BY title) "
        "ORDER BY org_id, id LIMIT ?", (n,)).fetchall()
    return [{"id": r["id"], "title": r["title"], "content": r["content"]} for r in rows]


def _query_for(doc: dict) -> str:
    """A natural query for a doc: prefer its title; fall back to its first sentence."""
    t = (doc["title"] or "").strip()
    if t and len(t) > 8:
        return t
    body = (doc["content"] or "").strip().replace("\n", " ")
    return body.split(". ")[0][:80]


def run(db: str, n_docs: int, n_targets: int) -> dict:
    from v2.core.retrieval.embedder import Embedder
    from v2.core.retrieval.model_descriptor import active_descriptor
    d = active_descriptor()
    emb = Embedder()
    if not emb.health_check():
        raise SystemExit(f"embedder health check failed for {d.ollama_name} (dim {d.dim}) — "
                         f"is Ollama up + model pulled? (EMBEDDING_MODEL={d.ollama_name})")
    docs = _load_docs(db, n_docs)
    if len(docs) < 2:
        raise SystemExit(f"need >=2 docs, got {len(docs)} from {db}")
    doc_vecs = [emb.embed_document(x["content"]) for x in docs]     # raw passages
    rows = []
    for i in range(min(n_targets, len(docs))):
        q = _query_for(docs[i])
        v_with = emb.embed_query(q)          # Instruct-wrapped (asymmetric, correct)
        v_without = emb.embed_document(q)    # raw, no prefix (the wrong wiring, for contrast)
        rows.append({
            "target": docs[i]["title"] or f"item {docs[i]['id']}",
            "query": q,
            "rank_with": rank_of(v_with, doc_vecs, i),
            "rank_without": rank_of(v_without, doc_vecs, i),
        })
    return {"descriptor": d.id, "dim": d.dim, "n_docs": len(docs),
            "rows": rows, "summary": summarize(rows)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Qwen prefix-wiring dry run")
    ap.add_argument("--db", required=True)
    ap.add_argument("--docs", type=int, default=10)
    ap.add_argument("--targets", type=int, default=5)
    args = ap.parse_args(argv)
    out = run(args.db, args.docs, args.targets)
    print(f"\nmodel={out['descriptor']}  dim={out['dim']}  passages={out['n_docs']}\n")
    print(f"{'target (query)':<48}  rank_WITH_prefix  rank_WITHOUT")
    print("-" * 80)
    for r in out["rows"]:
        print(f"{r['target'][:46]:<48}  {r['rank_with']:^16}  {r['rank_without']:^12}")
    s = out["summary"]
    print("-" * 80)
    print(f"top-1 hits:  WITH prefix {s['top1_with']}/{s['n']}   |   WITHOUT prefix "
          f"{s['top1_without']}/{s['n']}")
    print(f"mean rank:   WITH prefix {s['mean_rank_with']:.2f}   |   WITHOUT prefix "
          f"{s['mean_rank_without']:.2f}")
    print("\n(WITH-prefix ranks at/above WITHOUT confirms the asymmetric prefix is wired + helping.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
