"""Sweep orphaned vectors (item + chunk). Dry-run default; --commit applies after a hardened backup."""
import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import get_connection           # noqa: E402
from v2.core.database import vector_gc                        # noqa: E402
from scripts._area_tag_migrate import hardened_backup         # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    conn = get_connection(args.db)
    item = vector_gc.count_orphan_item_vectors(conn)
    chunk = vector_gc.count_orphan_chunk_vectors(conn)
    print(f"orphan vectors: item={item} chunk={chunk}")
    if not args.commit:
        print("dry-run — pass --commit to delete (a hardened backup is taken first).")
        return
    hardened_backup(args.db, "gc-vectors")
    d1 = vector_gc.sweep_orphan_item_vectors(conn)
    d2 = vector_gc.sweep_orphan_chunk_vectors(conn)
    conn.commit()
    vector_gc.assert_no_orphans(conn)
    print(f"deleted item={d1} chunk={d2}; invariant OK (0 orphans).")


if __name__ == "__main__":
    main()
