#!/usr/bin/env python
"""One-off gated fix: relabel duplicate-HOME faculty edges to ``category='affiliated'``.

14 faculty have two active ``has_role``/``category='faculty'`` (home) edges to distinct orgs — a
home-appointment-only violation ([[feedback_home_appointment_only]]) that leaks a cross-listed person
into the wrong roster (e.g. "who are MTSM faculty" wrongly returns Guiling Wang, whose real home is
Computer Science). This relabels the STRAY home edge to the ``affiliated`` tier (relabel, is_active=1
— the affiliation is preserved, not deleted), so ``faculty_in_department`` (which filters
``category='faculty'``) stops listing them while the relationship stays discoverable.

RULE (deterministic, DB-only, auditable) — SCOPED:
  1. SCOPE to persons with >1 active ``faculty`` edge (the multi-home set). Applied GLOBALLY the rule
     corrupts data (28 single-home faculty whose KB is filed under a different org_id — the HCAD
     host-vs-people split — plus 5 Theater faculty with 0 KB items would be wrongly demoted). The
     scope confines it to exactly the 14.
  2. WITHIN scope: each person's ``knowledge_items`` are filed under their single true HOME org
     (KB side is correct). KEEP the ``faculty`` edge whose org is in that KB-home set; relabel the
     rest → ``affiliated``.
  3. HARD GUARD: require exactly ONE keep. 0 keeps or >1 keeps → SKIP that person (logged), never
     demote both/neither. Protects future data (none trip today).

Prerequisite: ``scripts/_edges_category_migrate.py --commit`` must have widened the ``edges`` CHECK to
allow ``'affiliated'`` first (else the UPDATE fails the STRICT constraint).

Dry-run by default (prints the full keep/demote diff + any skips); ``--commit`` takes a hardened
backup first. Idempotent: once demoted, a person falls out of the >1 scope → re-run changes nothing.
Spec: docs/superpowers/specs/2026-07-05-affiliated-faculty-category-design.md
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection


def _scoped_people(conn: sqlite3.Connection) -> list[tuple[int, str, str]]:
    """(person_node_id, key, name) for every ACTIVE person with >1 active faculty edge."""
    return conn.execute(
        "SELECT e.src_id, p.key, p.name FROM edges e JOIN nodes p ON p.id=e.src_id "
        "WHERE e.type='has_role' AND e.category='faculty' AND e.is_active=1 AND p.is_active=1 "
        "GROUP BY e.src_id, p.key, p.name HAVING COUNT(DISTINCT e.dst_id) > 1 "
        "ORDER BY p.name").fetchall()


def _faculty_edges(conn: sqlite3.Connection, pid: int) -> list[tuple[int, str, int | None]]:
    """(edge_id, org_name, edge_org_id) for a person's active faculty edges. edge_org_id is the
    org NODE's attrs.org_id (the organizations.id), coerced to int, or None if absent."""
    out = []
    for eid, oname, raw_org_id in conn.execute(
            "SELECT e.id, o.name, o.attrs->>'org_id' FROM edges e JOIN nodes o ON o.id=e.dst_id "
            "WHERE e.src_id=? AND e.type='has_role' AND e.category='faculty' AND e.is_active=1",
            (pid,)):
        try:
            org_id = int(raw_org_id) if raw_org_id is not None else None
        except (TypeError, ValueError):
            org_id = None
        out.append((eid, oname, org_id))
    return out


def _kb_home_org_ids(conn: sqlite3.Connection, key: str) -> set[int]:
    """The set of organizations.id under which this person's ACTIVE knowledge_items are filed —
    their true home(s). Same org definition faculty_in_department's KB branch uses."""
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT org_id FROM knowledge_items "
        "WHERE is_active=1 AND org_id IS NOT NULL AND metadata->>'entity_id'=?", (key,))}


def plan_changes(conn: sqlite3.Connection):
    """Return (changes, skipped). changes: [{key,name,keep_org,demote:[(edge_id,org)]}].
    skipped: [{key,name,reason,keeps:[org],demote_candidates:[org]}] — persons the guard refused."""
    changes, skipped = [], []
    for pid, key, name in _scoped_people(conn):
        edges = _faculty_edges(conn, pid)
        home = _kb_home_org_ids(conn, key)
        keeps = [(eid, o, oid) for (eid, o, oid) in edges if oid is not None and oid in home]
        demote = [(eid, o, oid) for (eid, o, oid) in edges if not (oid is not None and oid in home)]
        if len(keeps) != 1:                       # HARD GUARD — never demote both/neither
            skipped.append({"key": key, "name": name,
                            "reason": f"{len(keeps)} keeps (need exactly 1)",
                            "keeps": [o for _e, o, _i in keeps],
                            "demote_candidates": [o for _e, o, _i in demote]})
            continue
        changes.append({"key": key, "name": name, "keep_org": keeps[0][1],
                        "demote": [(eid, o) for eid, o, _i in demote]})
    return changes, skipped


def apply_changes(conn: sqlite3.Connection, changes) -> int:
    """Relabel every planned demote edge → category='affiliated'. Returns edges changed. The caller
    owns the transaction/commit (gated workflow)."""
    n = 0
    for ch in changes:
        for eid, _org in ch["demote"]:
            conn.execute("UPDATE edges SET category='affiliated', updated_at=datetime('now') "
                         "WHERE id=?", (eid,))
            n += 1
    return n


def _print_plan(changes, skipped) -> None:
    print(f"\n{len(changes)} person(s) to fix — keep HOME faculty, relabel the stray → affiliated:\n")
    for ch in changes:
        strays = "; ".join(f"{o} (edge {eid})" for eid, o in ch["demote"])
        print(f"  {ch['name']:<22} KEEP {ch['keep_org']:<34} → AFFILIATED: {strays}")
    if skipped:
        print(f"\n{len(skipped)} person(s) SKIPPED by the guard (not exactly 1 keep):")
        for s in skipped:
            print(f"  {s['name']:<22} {s['reason']} | keeps={s['keeps']} demote={s['demote_candidates']}")
    total = sum(len(ch["demote"]) for ch in changes)
    print(f"\n=> {total} edge(s) would be relabeled across {len(changes)} person(s).")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)

    conn = get_connection(args.db)
    # Guard: the CHECK must already allow 'affiliated' (run _edges_category_migrate.py first).
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='edges'").fetchone()
    if row and "'affiliated'" not in row[0]:
        print("ERROR: edges.category CHECK does not allow 'affiliated' yet — run "
              "scripts/_edges_category_migrate.py --commit first.")
        return 1

    changes, skipped = plan_changes(conn)
    _print_plan(changes, skipped)
    if not args.commit:
        print("\n(dry run — pass --commit to apply; a hardened backup is taken first)")
        return 0

    bkp = hardened_backup(args.db, "pre-affiliated-fix")
    print(f"\nbackup: {bkp.name}")
    n = apply_changes(conn, changes)
    conn.commit()
    print(f"committed: {n} edge(s) relabeled to 'affiliated' across {len(changes)} person(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
