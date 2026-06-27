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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="Re-chunk all (else only items with no chunks).")
    args = ap.parse_args()

    d = active_descriptor()
    emb = Embedder(model=d.ollama_name)
    conn = get_connection(args.db)

    exclude = tuple(DEFAULT_EXCLUDE_TYPES)
    placeholders = ",".join("?" * len(exclude))
    base = (f"SELECT id, org_id, type, content FROM knowledge_items "
            f"WHERE is_active = 1 AND type NOT IN ({placeholders})")
    params: list = list(exclude)
    if not args.force:
        base += " AND id NOT IN (SELECT DISTINCT parent_id FROM knowledge_chunks)"
    rows = conn.execute(base + " ORDER BY id", params).fetchall()
    if args.limit:
        rows = rows[:args.limit]

    # Phase 1 — chunk + insert chunk rows; collect embed work.
    pending = []  # (chunk_id, embed_input, org_id, type, parent_id)
    for r in rows:
        drop_item_chunks(conn, r["id"])
        for ordinal, ch in enumerate(chunk_text(r["content"] or "", d)):
            cur = conn.execute(
                "INSERT INTO knowledge_chunks(parent_id, source_key, ordinal, text, content_hash, model_id) "
                "VALUES (?,?,?,?,?,?)",
                (r["id"], f"item:{r['id']}", ordinal, ch, content_hash(ch, d.id), d.id),
            )
            embed_input = d.doc_prefix + d.truncate_to_tokens(ch, d.context_window)
            pending.append((cur.lastrowid, embed_input, r["org_id"], r["type"], r["id"]))
    conn.commit()
    print(f"chunked items={len(rows)} chunks={len(pending)}", flush=True)

    # Phase 2 — batch embed + write vectors.
    written = 0
    for i in range(0, len(pending), BATCH):
        batch = pending[i:i + BATCH]
        vecs = emb._embed_batch([b[1] for b in batch])
        for (chunk_id, _ei, org_id, typ, pid), v in zip(batch, vecs):
            norm = Embedder.normalize(v)
            if norm is None:
                continue
            conn.execute(
                "INSERT INTO knowledge_chunk_vectors(chunk_id, embedding, org_id, type, parent_id) "
                "VALUES (?,?,?,?,?)",
                (chunk_id, sqlite_vec.serialize_float32(norm), org_id, typ, pid),
            )
            written += 1
        conn.commit()
        print(f"  embedded {min(i + BATCH, len(pending))}/{len(pending)}", flush=True)

    swept = vector_gc.sweep_orphan_chunk_vectors(conn) + vector_gc.sweep_orphan_item_vectors(conn)
    conn.commit()
    vector_gc.assert_no_orphans(conn)
    print(f"DONE items={len(rows)} chunks={len(pending)} vectors={written} swept={swept}; invariant OK")


if __name__ == "__main__":
    main()
