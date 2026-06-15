#!/usr/bin/env python
"""Run the explore() KG gathering engine from an anchored entry point.

DEFAULT START is the YWCC people hub. Writes nodes/edges/raw_pages/frontier into the
graph layer. Point --db at a COPY first (dev run) before the live DB; the live KB is
shared with the running bot.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all, get_connection
from v2.core.ingestion.explore import explore, http_fetch


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--reset", action="store_true",
                    help="clear the graph layer first (re-derive from scratch; resets "
                         "change-detection so every page re-extracts)")
    ap.add_argument("--frontier", action="store_true",
                    help="instead of a hub crawl, process pending frontier next-steps "
                         "(personal sites) into 'webpage' knowledge_items")
    args = ap.parse_args()

    create_all(args.db)                       # ensure graph tables exist (idempotent)
    conn = get_connection(args.db)
    if args.reset:
        # Re-derive everything the CRAWLER produces (graph layer + crawler knowledge_items
        # + their vectors). Manual content (created_by!='crawler', e.g. GSA/MMI) is left
        # untouched. FK-safe order; FTS rows self-clean via the knowledge_items delete trigger.
        ki = [r[0] for r in conn.execute(
            "SELECT id FROM knowledge_items WHERE created_by='crawler'")]
        conn.executemany("DELETE FROM knowledge_vectors WHERE item_id=?", [(i,) for i in ki])
        conn.execute("DELETE FROM knowledge_items WHERE created_by='crawler'")
        # Graph: clear only CRAWLER-derived data. Keep manual (source!='crawler') nodes/edges
        # — e.g. a manually-added President — and keep Org nodes (re-derived each gather and
        # referenced by manual appointments). FK-safe order.
        conn.execute("DELETE FROM page_nodes")
        conn.execute("DELETE FROM frontier")
        conn.execute("DELETE FROM raw_pages")
        conn.execute("DELETE FROM edges WHERE source='crawler'")
        conn.execute("DELETE FROM nodes WHERE source='crawler' AND type!='Org'")
        conn.commit()
        print(f"reset: cleared crawler graph + {len(ki)} crawler knowledge_items "
              f"(manual content + org tree kept)")

    if args.frontier:
        from v2.core.ingestion.explore import process_frontier
        st = process_frontier(conn, http_fetch)
        print(f"\nfrontier stats: {st}")
        print("  webpage items:",
              conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE type='webpage' "
                           "AND is_active=1").fetchone()[0])
        print("  pending frontier left:",
              conn.execute("SELECT COUNT(*) FROM frontier WHERE status='pending'").fetchone()[0])
        return 0

    st = explore(conn, http_fetch, depth=args.depth)
    print(f"\nexplore stats: {st}")

    from v2.core.ingestion.explore import reconcile_departures
    dep = reconcile_departures(conn)        # M3: retire people who left / clean moved KB
    print(f"departures (M3): appointments retired={st.departed}, "
          f"people removed={dep['departed_people']}, stale items retired={dep['items_retired']}")

    print("\n=== graph summary ===")
    for typ, n in conn.execute(
            "SELECT type, COUNT(*) FROM nodes WHERE is_active=1 GROUP BY type ORDER BY type"):
        print(f"  nodes {typ:14} {n}")
    for typ, n in conn.execute(
            "SELECT type, COUNT(*) FROM edges WHERE is_active=1 GROUP BY type ORDER BY type"):
        print(f"  edges {typ:14} {n}")
    print(f"  raw_pages {conn.execute('SELECT COUNT(*) FROM raw_pages').fetchone()[0]}"
          f"  frontier {conn.execute('SELECT COUNT(*) FROM frontier').fetchone()[0]}")

    print("\n=== people with >1 active appointment (cross-path / dual role) ===")
    rows = conn.execute(
        "SELECT n.name, COUNT(*) c FROM nodes n JOIN edges e ON e.src_id=n.id "
        "WHERE n.type='Person' AND e.type='has_role' AND e.is_active=1 "
        "GROUP BY n.id HAVING c>1 ORDER BY c DESC, n.name").fetchall()
    for name, c in rows:
        print(f"  {name}: {c} appointments")

    from scripts.verify_kg import verify_kg
    issues = verify_kg(conn)
    print("\n=== mandatory alignment check (listing ⟷ KB) ===")
    if issues:
        print(f"  ✗ {len(issues)} problem(s):")
        for i in issues[:20]:
            print("   -", i)
    else:
        print("  ✓ KG aligned with KB — no mis-filed or missing people")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
