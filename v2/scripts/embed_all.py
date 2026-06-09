"""GSA Gateway v2.0 — embedding pass (Step 3).

Embeds every active ``knowledge_items`` row into the ``knowledge_vectors`` vec0
table using Ollama's ``nomic-embed-text``. Resumable: re-running only embeds
items not already present. Safe to run against the LIVE ``gsa_gateway.db`` —
``knowledge_vectors`` is a v2-only table the v1 bot never reads.

Embedding conventions (must match v1 so vectors are query-compatible):
  * Documents are embedded with the ``search_document: `` prefix; queries (the
    smoke tests, and Step 4's retriever) use ``search_query: ``.
  * Vectors are L2-normalized before storage. The vec0 column is plain
    ``FLOAT[768]`` (default L2 distance); on normalized vectors L2 ranking is
    identical to cosine ranking, so we get cosine behaviour without changing the
    approved schema. The retriever must normalize query vectors the same way.

Usage:
    python v2/scripts/embed_all.py              # embed all missing (resumable)
    python v2/scripts/embed_all.py --force      # wipe + re-embed everything
    python v2/scripts/embed_all.py --validate   # coverage + smoke tests, no embed
    python v2/scripts/embed_all.py --item 42    # re-embed a single item id
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import sqlite_vec  # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from v2.core.database.schema import get_connection  # noqa: E402

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
EMBED_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")
EMBED_DIM = 768
LIVE_DB = str(REPO_ROOT / "gsa_gateway.db")

GREEN, YELLOW, RED, DIM, RESET = "\033[92m", "\033[93m", "\033[91m", "\033[2m", "\033[0m"


# ─────────────────────────────────────────────────────────────────────────────
# Ollama
# ─────────────────────────────────────────────────────────────────────────────

def _post_embed(text: str, timeout: int = 30) -> list[float] | None:
    payload = json.dumps({"model": EMBED_MODEL, "input": text}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/embed", data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    embeddings = data.get("embeddings")
    if not embeddings or not embeddings[0]:
        return None
    return embeddings[0]


def embed_document(text: str, timeout: int = 30) -> list[float] | None:
    return _post_embed(f"search_document: {text.strip()[:2000]}", timeout)


def embed_query(text: str, timeout: int = 30) -> list[float] | None:
    return _post_embed(f"search_query: {text.strip()[:2000]}", timeout)


def normalize(vec: list[float]) -> list[float] | None:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return None
    return [v / norm for v in vec]


def health_check() -> None:
    """Verify Ollama + model + a real 768-dim embedding before touching the db."""
    print("Ollama health check:")
    # 1. server up + model list
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=10) as resp:
            tags = json.loads(resp.read())
    except urllib.error.URLError as e:
        sys.exit(f"{RED}  ✗ Ollama not reachable at {OLLAMA_URL} ({e}). Start it: `ollama serve`{RESET}")
    models = [m.get("name", "") for m in tags.get("models", [])]
    print(f"  ✓ Ollama running at {OLLAMA_URL}")

    # 2. model available (match with or without :tag)
    if not any(m == EMBED_MODEL or m.startswith(EMBED_MODEL + ":") for m in models):
        sys.exit(f"{RED}  ✗ Model '{EMBED_MODEL}' not found. Available: {models}. "
                 f"Pull it: `ollama pull {EMBED_MODEL}`{RESET}")
    print(f"  ✓ Model '{EMBED_MODEL}' available")

    # 3. test embed
    try:
        vec = embed_document("health check", timeout=15)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"{RED}  ✗ Test embedding failed: {e}{RESET}")
    if vec is None:
        sys.exit(f"{RED}  ✗ Test embedding returned no vector.{RESET}")
    print(f"  ✓ Test embed 'health check' -> vector returned")

    # 4. dimension
    if len(vec) != EMBED_DIM:
        sys.exit(f"{RED}  ✗ Expected {EMBED_DIM} dims, got {len(vec)}.{RESET}")
    print(f"  ✓ Vector is {len(vec)} dimensions\n")


# ─────────────────────────────────────────────────────────────────────────────
# Embedding pass
# ─────────────────────────────────────────────────────────────────────────────

def _store_vector(conn, item_id: int, vec: list[float]) -> bool:
    norm = normalize(vec)
    if norm is None:
        return False
    conn.execute("DELETE FROM knowledge_vectors WHERE item_id = ?", (item_id,))
    conn.execute(
        "INSERT INTO knowledge_vectors(item_id, embedding) VALUES (?, ?)",
        (item_id, sqlite_vec.serialize_float32(norm)),
    )
    return True


def _targets(conn, force: bool, single: int | None):
    if single is not None:
        return conn.execute(
            "SELECT id, type, title, search_text FROM knowledge_items "
            "WHERE id = ? AND is_active = 1", (single,)
        ).fetchall()
    if force:
        conn.execute("DELETE FROM knowledge_vectors")
        where = "is_active = 1"
    else:
        where = ("is_active = 1 AND id NOT IN (SELECT item_id FROM knowledge_vectors)")
    return conn.execute(
        f"SELECT id, type, title, search_text FROM knowledge_items WHERE {where} ORDER BY id"
    ).fetchall()


def run_embedding(conn, force: bool, single: int | None) -> tuple[int, int, list]:
    total_active = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE is_active = 1"
    ).fetchone()[0]
    already = conn.execute("SELECT COUNT(*) FROM knowledge_vectors").fetchone()[0]
    targets = _targets(conn, force, single)
    n = len(targets)

    if n == 0:
        print(f"Nothing to embed — {already}/{total_active} already covered.\n")
        return 0, [], total_active

    skipped_note = f"  (skipping {already} already done)" if not force and not single else ""
    print(f"Embedding {n} item(s){skipped_note}\n")

    succeeded, failed = 0, []
    for i, row in enumerate(targets, 1):
        title = (row["title"] or row["search_text"] or "").strip().replace("\n", " ")
        label = f"{DIM}[{i:>3}/{n}]{RESET} {row['type']}: {title[:48]}"
        vec = None
        for attempt in (1, 2):  # try once, retry once
            try:
                vec = embed_document(row["search_text"])
                if vec is not None:
                    break
            except Exception:  # noqa: BLE001 - timeout/conn; retry then skip
                time.sleep(0.3)
        if vec is not None and _store_vector(conn, row["id"], vec):
            succeeded += 1
            print(f"{label} {GREEN}✅{RESET}")
        else:
            failed.append((row["id"], row["type"], title[:48]))
            print(f"{label} {RED}✗ skipped{RESET}")
        time.sleep(0.05)

    conn.commit()
    return succeeded, failed, total_active


# ─────────────────────────────────────────────────────────────────────────────
# Smoke tests
# ─────────────────────────────────────────────────────────────────────────────

SMOKE_TESTS = [
    ("Conference funding", "how do I get money for conference", 3,
     "expect travel award content"),
    ("GSA finances contact", "who is in charge of GSA finances", 3,
     "expect Mohith Oduru / VP Finance"),
    ("MMI workshop", "workshop on multimedia research", 3,
     "expect MMI content, not GSA"),
    ("Cross domain funding+workshop", "funding to attend the workshop", 5,
     "expect a mix of GSA + MMI"),
    ("Club budget violations", "club budget violations", 3,
     "expect club finance policy"),
]


def knn(conn, query: str, k: int):
    qvec = normalize(embed_query(query))
    rows = conn.execute(
        """
        SELECT ki.title, ki.type, o.slug AS org, v.distance
        FROM (
            SELECT item_id, distance FROM knowledge_vectors
            WHERE embedding MATCH ? ORDER BY distance LIMIT ?
        ) v
        JOIN knowledge_items ki ON ki.id = v.item_id
        JOIN organizations o ON o.id = ki.org_id
        ORDER BY v.distance
        """,
        (sqlite_vec.serialize_float32(qvec), k),
    ).fetchall()
    return rows


def run_smoke_tests(conn) -> None:
    print("Vector Search Smoke Tests:\n")
    for idx, (name, query, k, hint) in enumerate(SMOKE_TESTS, 1):
        print(f"Test {idx} — {name}:  (query: \"{query}\")")
        for j, r in enumerate(knn(conn, query, k)):
            mark = "  <-- top" if j == 0 else ""
            print(f"  → [{r['org']}/{r['type']}] {r['title']}  (distance: {r['distance']:.3f}){mark}")
        print(f"  {DIM}({hint}){RESET}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Report + main
# ─────────────────────────────────────────────────────────────────────────────

def print_report(conn, succeeded, failed, total_active):
    embedded = conn.execute("SELECT COUNT(*) FROM knowledge_vectors").fetchone()[0]
    pct = (embedded / total_active * 100) if total_active else 0
    bar = "═" * 51
    print("\n" + bar)
    print("  Embedding Report")
    print(bar)
    print(f"  Total active knowledge_items: {total_active}")
    print(f"  Successfully embedded this run: {succeeded}")
    print(f"  Failed (skipped): {len(failed)}")
    for iid, itype, title in failed:
        print(f"    - id={iid} [{itype}] {title}")
    print(f"\n  Coverage: {embedded}/{total_active} ({pct:.0f}%)\n")

    run_smoke_tests(conn)

    status = (f"{GREEN}✅ Ready for Step 4 (V2 Retriever){RESET}" if embedded == total_active
              else f"{YELLOW}⚠ Coverage incomplete — re-run to embed the {len(failed)} skipped item(s){RESET}")
    print(f"  Status: {status}")
    print(bar + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Embed knowledge_items into knowledge_vectors (Step 3).")
    ap.add_argument("db_path", nargs="?", default=LIVE_DB, help="Target db (default: live gsa_gateway.db)")
    ap.add_argument("--force", action="store_true", help="Wipe and re-embed everything.")
    ap.add_argument("--validate", action="store_true", help="Coverage + smoke tests only; no embedding.")
    ap.add_argument("--item", type=int, default=None, help="Re-embed a single item id.")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db_path):
        ap.error(f"Database not found: {args.db_path}")

    health_check()
    conn = get_connection(args.db_path)
    try:
        if args.validate:
            total = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1").fetchone()[0]
            print_report(conn, 0, [], total)
            return
        succeeded, failed, total = run_embedding(conn, args.force, args.item)
        print_report(conn, succeeded, failed, total)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
