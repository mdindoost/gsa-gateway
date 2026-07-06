#!/usr/bin/env python3
"""One-off gated data correction: Shantanu Sharma's research areas.

His NJIT profile lists 5 <br>-delimited research interests, but the crawler's
_clean() flattens <br> to a space and _split_areas only splits on ','/';' — so
with no commas between his items the whole list is one over-long token, rejected
as an area. Result: ZERO `researches` edges (his interests survived only as a
collapsed `research_statement` blob, id 1776). This creates the 5 real areas as
the SAME artifacts a correctly-crawled faculty has (structured `researches` edges,
source='crawler'), so his FacultyFolio "Areas of focus" chips + the bot's
`research_of_person` fill in. Owner-authoritative list (verified on the live page).

QUICK PATCH ONLY — the parser bug is intentionally left (owner: "do it on the DB,
no worry about crawling", 2026-07-06). A future re-crawl would re-collapse; re-run
this then. Mirrors scripts/_gwang_research_area_fix.py exactly. The collapsed
`research_statement` id 1776 is LEFT active (harmless; FacultyFolio's area path
ignores it). Dry-run by default; --commit writes (hardened backup on the live DB).
"""
import argparse, os, sqlite3, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts._area_tag_migrate import hardened_backup
from v2.core.graph.store import (upsert_node, upsert_edge,
                                 active_edge_ids_from, deactivate_edges)
from v2.core.graph.project import area_key

PERSON_KEY = "people.njit.edu/profile/ss797"
TARGET_AREAS = [
    "Databases",
    "Secure data processing",
    "Trustworthy IoT data-driven systems",
    "Cloud computing",
    "ML in databases and secure model learning",
]
LIVE_DB = os.path.join(ROOT, "gsa_gateway.db")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=LIVE_DB)
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    if args.commit and os.path.abspath(args.db) == os.path.abspath(LIVE_DB):
        hardened_backup(args.db, "ss797-research-area-fix")

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    prow = c.execute("SELECT id, name FROM nodes WHERE type='Person' AND key=?",
                     (PERSON_KEY,)).fetchone()
    assert prow, f"Person {PERSON_KEY} not found"
    pid = prow["id"]

    print("=== BEFORE: %s (%s) researches edges ===" % (prow["name"], PERSON_KEY))
    for e in c.execute("SELECT ra.name, e.source, e.is_active FROM edges e "
                       "JOIN nodes ra ON ra.id=e.dst_id "
                       "WHERE e.src_id=? AND e.type='researches' ORDER BY e.id", (pid,)):
        print("   ", dict(e))
    print("   (none above = zero areas — the bug)")

    # structured `researches` edges, source='crawler' (mirror the crawler's own artifacts)
    keep = set()
    for a in TARGET_AREAS:
        anode = upsert_node(conn, type="ResearchArea", key=area_key(a), name=a, source="crawler")
        keep.add(upsert_edge(conn, src_id=pid, type="researches", dst_id=anode,
                             area_source="structured", source="crawler"))
    sweep = active_edge_ids_from(conn, pid, type="researches", source="crawler") - keep

    print("\n=== AFTER (proposed) ===")
    print("KEEP (5 new): edge ids", sorted(keep))
    print("DEACTIVATE (stale crawler edges, should be none):", sorted(sweep))
    ext = [r["id"] for r in c.execute(
        "SELECT id FROM edges WHERE src_id=? AND type='researches' AND source!='crawler' "
        "AND is_active=1", (pid,)).fetchall()]
    print("UNTOUCHED non-crawler edges:", ext)
    deactivate_edges(conn, sweep)

    # show the resulting active area chips
    print("\nResulting active areas (what FacultyFolio will show):")
    for r in c.execute("SELECT ra.name FROM edges e JOIN nodes ra ON ra.id=e.dst_id "
                       "WHERE e.src_id=? AND e.type='researches' AND e.is_active=1 ORDER BY e.id",
                       (pid,)):
        print("   +", r["name"])

    if args.commit:
        conn.commit()
        print("\n✅ COMMITTED to", args.db)
    else:
        conn.rollback()
        print("\n(dry-run — nothing written. Re-run with --commit to apply.)")
    conn.close()


if __name__ == "__main__":
    main()
