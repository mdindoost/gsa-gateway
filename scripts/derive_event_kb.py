#!/usr/bin/env python3
"""Gated re-derive: sync all GSA events from the OPS DB into KB event_info items.

Usage (dry-run — default, safe to inspect):
    python scripts/derive_event_kb.py --kb gsa_gateway.db --ops gsa_gateway_ops.db

Usage (write to KB):
    python scripts/derive_event_kb.py --kb gsa_gateway.db --ops gsa_gateway_ops.db --commit

Usage (also trigger embed after derive):
    python scripts/derive_event_kb.py --commit --embed

This is a REPAIR / REBUILD tool.  Normal day-to-day derivation happens
automatically in the dashboard's _create_event handler.  Use this script:
  - after a KB rebuild (re-derive all events from scratch)
  - to repair a gap left by a failed _create_event KB write (MED-9)
  - to back-fill the natural_key onto pre-Phase-3 event_info rows (MED-8)

Gated (follows project convention):
  * default = dry-run (prints planned derives, writes nothing)
  * --commit to actually write; takes a hardened_backup first
  * --embed to run embed_all.py after (so new items get vectors)

Idempotent: running --commit twice yields 0 net-new event_info rows.

Cross-DB ordering: reads OPS (events) → writes KB (knowledge_items).
Never writes back to OPS.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from v2.core.database.schema import get_connection, get_ops_connection
from v2.core.publishing.event_projection import derive_event_kb


def _count_gsa_events(ops_conn, org_slugs=("gsa",)) -> int:
    placeholders = ",".join("?" * len(org_slugs))
    return ops_conn.execute(
        f"SELECT COUNT(*) FROM events WHERE org_slug IN ({placeholders})",
        tuple(org_slugs),
    ).fetchone()[0]


def _count_derived_items(kb_conn, org_slug="gsa") -> int:
    return kb_conn.execute(
        "SELECT COUNT(*) FROM knowledge_items "
        "WHERE type='event_info' AND json_extract(metadata,'$.org_slug')=?",
        (org_slug,),
    ).fetchone()[0]


def main():
    parser = argparse.ArgumentParser(
        description="Re-derive GSA event_info knowledge_items from OPS events."
    )
    parser.add_argument("--kb", required=True, help="Path to the Knowledge DB")
    parser.add_argument("--ops", required=True, help="Path to the OPS DB")
    parser.add_argument(
        "--commit", action="store_true",
        help="Actually write to KB (default: dry-run, no writes)"
    )
    parser.add_argument(
        "--embed", action="store_true",
        help="Run embed_all.py after derive (only with --commit)"
    )
    parser.add_argument(
        "--org-slugs", nargs="+", default=["gsa"],
        help="Org slugs to derive for (default: gsa)"
    )
    args = parser.parse_args()

    kb_path = args.kb
    ops_path = args.ops
    org_slugs = tuple(args.org_slugs)

    ops_conn = get_ops_connection(ops_path)
    kb_conn = get_connection(kb_path)

    n_events = _count_gsa_events(ops_conn, org_slugs)
    n_existing = sum(_count_derived_items(kb_conn, slug) for slug in org_slugs)

    if not args.commit:
        # --- Dry-run: report planned work, write nothing ---
        print(f"[dry-run] Would derive {n_events} OPS event(s) for orgs: {', '.join(org_slugs)}")
        print(f"[dry-run] KB currently has {n_existing} derived event_info item(s)")
        print("[dry-run] Pass --commit to write. No changes made.")
        ops_conn.close()
        kb_conn.close()
        return

    # --- Commit mode: backup first, then derive ---
    try:
        from scripts._area_tag_migrate import hardened_backup
        pre = hardened_backup(kb_path, "pre-derive-event-kb")
        print(f"Backup written: {pre}")
    except Exception as exc:  # noqa: BLE001
        print(f"[warning] Could not take hardened_backup: {exc} — proceeding anyway")

    print(f"Deriving {n_events} OPS event(s) for orgs: {', '.join(org_slugs)} ...")
    totals = derive_event_kb(ops_conn, kb_conn, org_slugs=org_slugs)
    print(
        f"Done: {totals['created']} created, "
        f"{totals['updated']} updated (back-filled), "
        f"{totals['deactivated']} deactivated."
    )

    ops_conn.close()
    kb_conn.close()

    if args.embed:
        import subprocess
        embed_script = str(REPO_ROOT / "v2" / "scripts" / "embed_all.py")
        print(f"Running embed: {embed_script}")
        subprocess.run([sys.executable, embed_script, "--db", kb_path], check=True)

    print("derive_event_kb complete.")


if __name__ == "__main__":
    main()
