"""Batch chunk-embed pass: chunk every active served item, embed via Ollama (batched),
write knowledge_chunks + knowledge_chunk_vectors, then GC orphans and assert the invariant.

Parallels embed_all.py but for the parent-document chunk tables. Resumable: without
--force, only items that have no chunks yet are processed. The embed step batches chunk
texts into one Ollama /api/embed call (list input) to cut wall-clock.

Usage:
    python3 v2/scripts/embed_chunks.py --db /tmp/copy.db --limit 50   # validate on a copy
    python3 v2/scripts/embed_chunks.py --db /tmp/copy.db              # full pass on a copy
"""
import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import sqlite_vec  # noqa: E402

from v2.core.database.schema import get_connection           # noqa: E402
from v2.core.database import vector_gc                        # noqa: E402
from v2.core.retrieval.chunker import chunk_text             # noqa: E402
from v2.core.retrieval.chunk_populate import drop_item_chunks, content_hash  # noqa: E402
from v2.core.retrieval.model_descriptor import active_descriptor             # noqa: E402
from v2.core.retrieval.embedder import Embedder              # noqa: E402
from v2.core.retrieval.retriever import DEFAULT_EXCLUDE_TYPES               # noqa: E402

BATCH = 64


def _coverage_holes(conn):
    """Active-parent chunks with no vector — the EXACT complement of invariant condition 2,
    so embedding all of these (successfully) drives condition 2 to 0 (convergence-by-complement).
    Model-blind by design; condition 4 (stale model_id) is the backstop."""
    return conn.execute(
        """
        SELECT c.id, c.text, i.org_id, i.type, c.parent_id
        FROM knowledge_chunks c JOIN knowledge_items i ON i.id = c.parent_id
        WHERE i.is_active = 1
          AND NOT EXISTS (SELECT 1 FROM knowledge_chunk_vectors cv WHERE cv.chunk_id = c.id)
        ORDER BY c.id
        """
    ).fetchall()


def _prepare(d, text):
    return d.doc_prefix + d.truncate_to_tokens(text, d.context_window)


def _write_vector(conn, chunk_id, raw, org_id, typ, parent_id) -> bool:
    norm = Embedder.normalize(raw)
    if norm is None:
        return False
    conn.execute(
        "INSERT INTO knowledge_chunk_vectors(chunk_id, embedding, org_id, type, parent_id) "
        "VALUES (?,?,?,?,?)",
        (chunk_id, sqlite_vec.serialize_float32(norm), org_id, typ, parent_id),
    )
    return True


def run_chunk_embed(conn, d, emb, *, batch=BATCH, attempts=3, backoff=0.5,
                    force=False, limit=None) -> dict:
    """Phase 1: chunk items lacking a current-model chunk. Phase 2: embed every active-parent
    unvectored chunk (new + previously-failed), batched, with per-slot retry on None/batch-exception
    and an outage-abort. Per-batch commit (durable progress). No GC / assert / exit here — main owns
    those. Returns counts + an `aborted` flag."""
    from v2.core.retrieval.embedder import embed_with_retry

    # ── Phase 1: create chunk rows for items that have no current-model chunk ──
    exclude = tuple(DEFAULT_EXCLUDE_TYPES)
    ph = ",".join("?" * len(exclude))
    sql = (f"SELECT id, content FROM knowledge_items WHERE is_active=1 AND type NOT IN ({ph})")
    params = list(exclude)
    if not force:
        # NON-model-scoped (original behavior): an item with only stale-model chunks is treated as
        # "has chunks" and skipped, so the stale rows survive for condition 4 to catch — model
        # changes are the --force path, not a plain re-run. Do NOT add a model_id filter here.
        sql += " AND id NOT IN (SELECT DISTINCT parent_id FROM knowledge_chunks)"
    sql += " ORDER BY id"
    items = conn.execute(sql, params).fetchall()
    if limit:
        items = items[:limit]
    chunked = 0
    for r in items:
        drop_item_chunks(conn, r["id"])
        for ordinal, ch in enumerate(chunk_text(r["content"] or "", d)):
            conn.execute(
                "INSERT INTO knowledge_chunks(parent_id, source_key, ordinal, text, content_hash, model_id) "
                "VALUES (?,?,?,?,?,?)",
                (r["id"], f"item:{r['id']}", ordinal, ch, content_hash(ch, d.id), d.id),
            )
            chunked += 1
    conn.commit()

    # ── Phase 2: embed all active-parent unvectored chunks (coverage-driven) ──
    holes = _coverage_holes(conn)
    starting_holes = len(holes)  # sampled AFTER Phase 1 — measures the embed work queue, not item coverage
    vectors = retried = failed = 0
    aborted = False
    for i in range(0, len(holes), batch):
        chunk_batch = holes[i:i + batch]
        inputs = [_prepare(d, c["text"]) for c in chunk_batch]
        try:
            vecs = emb._embed_batch(inputs)
        except Exception:  # noqa: BLE001 - C2: batch-level conn reset -> degrade to per-slot retry
            vecs = [None] * len(chunk_batch)
        batch_written = 0
        for c, prepared, raw in zip(chunk_batch, inputs, vecs):
            if raw is None:
                retried += 1
                raw = embed_with_retry(lambda p=prepared: emb._embed(p), attempts=attempts, backoff=backoff)
            if raw is not None and _write_vector(conn, c["id"], raw, c["org_id"], c["type"], c["parent_id"]):
                vectors += 1
                batch_written += 1
            else:
                failed += 1
        conn.commit()
        if chunk_batch and batch_written == 0:   # N1: a whole batch failed even after retries -> outage
            aborted = True
            break
    return {"chunked": chunked, "vectors": vectors, "retried": retried,
            "failed": failed, "aborted": aborted, "starting_holes": starting_holes}


def main(argv=None, emb=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="Re-chunk all (else only items with no current-model chunk).")
    args = ap.parse_args(argv)

    d = active_descriptor()
    if emb is None:
        emb = Embedder(model=d.ollama_name)
        if not emb.health_check():               # fast-fail before any DB write
            print("ERROR: embedder health check failed (Ollama/model unavailable).")
            return 2
    conn = get_connection(args.db)

    res = run_chunk_embed(conn, d, emb, force=args.force, limit=args.limit)

    if res["aborted"]:
        print(f"ABORTED (outage): chunked={res['chunked']} vectors={res['vectors']} "
              f"retried={res['retried']} failed={res['failed']} starting_holes={res['starting_holes']} "
              f"— progress committed; re-run when Ollama is healthy.")
        return 1

    swept = (vector_gc.sweep_orphan_chunk_vectors(conn)
             + vector_gc.sweep_orphan_item_vectors(conn)
             + vector_gc.sweep_orphan_chunk_rows(conn))
    conn.commit()
    print(f"chunked={res['chunked']} vectors={res['vectors']} retried={res['retried']} "
          f"failed={res['failed']} starting_holes={res['starting_holes']} gc_swept={swept}", flush=True)

    if res["failed"] > 0:
        print(f"INCOMPLETE: {res['failed']} chunk(s) still unvectored — re-run to converge (no --force needed).")
        return 1

    vector_gc.assert_chunk_invariant(conn, d)
    print(f"DONE; invariant OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
