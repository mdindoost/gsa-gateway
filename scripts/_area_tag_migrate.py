"""Shared core for the research-area-tag metadata migrations (backfill + repair).

ONE definition of "the areas of a row" — njit_adapter._split_areas, the canonical
ingestion path (paren-aware, prose/single-token filtered) — and ONE hardened,
WAL-safe, integrity-checked backup + dry-run runner. The two one-off CLIs are thin
wrappers over this, so they can never drift apart on what an area is or how a write
is guarded.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:           # allow `from v2...` when run as a script
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.njit_adapter import _split_areas


def canonical_areas(content: str) -> list[str]:
    """The discrete area list a row SHOULD carry, derived from its stored
    'Research areas of X: ...' content via the canonical ingestion splitter — the same
    definition decompose used to produce it. A ';' inside parens stays within one area;
    prose / single-token content collapses to [] (precision over recall)."""
    if ": " not in content:
        return []
    return _split_areas(content.split(": ", 1)[1])


def hardened_backup(db_path: str, label: str, keep: int = 10) -> Path:
    """Mandatory pre-write snapshot via the SQLite online-backup API (safe with the live
    bot's WAL open), integrity-checked, rotated to the last ``keep``. Raises if the
    snapshot is not 'ok' — we never write without a good backup."""
    bdir = REPO / ".backups"
    bdir.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = bdir / f"gsa_gateway.{ts}.{label}.db"
    src = sqlite3.connect(db_path)
    d = sqlite3.connect(str(dst))
    try:
        with d:
            src.backup(d)
        if d.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("integrity_check failed on the backup")
    finally:
        d.close()
        src.close()
    for old in sorted(bdir.glob(f"gsa_gateway.*.{label}.db"))[:-keep]:
        old.unlink(missing_ok=True)
    return dst


def run_area_migration(db: str, commit: bool, label: str, needs) -> int:
    """Recompute canonical areas for every active research_areas row and update those
    where ``needs(old_areas, new_areas)`` is true. Dry run by default; --commit takes a
    hardened backup first. Idempotent: re-running with no qualifying rows writes nothing."""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, content, metadata FROM knowledge_items "
        "WHERE type='research_areas' AND is_active=1").fetchall()

    planned = []
    for r in rows:
        meta = json.loads(r["metadata"])
        old = meta.get("areas") or []
        new = canonical_areas(r["content"])
        if needs(old, new):
            meta["areas"] = new
            planned.append((r["id"], json.dumps(meta), old, new))

    print(f"{len(rows)} active research_areas items; {len(planned)} to update.")
    for _id, _m, old, new in planned[:8]:
        print(f"  id={_id}:\n    OLD {old}\n    NEW {new}")

    if not commit:
        print("DRY RUN — pass --commit to write.")
        return 0

    dst = hardened_backup(db, label)
    print(f"backup: {dst} (integrity ok)")
    conn.executemany("UPDATE knowledge_items SET metadata=? WHERE id=?",
                     [(m, i) for i, m, _o, _n in planned])
    conn.commit()
    print(f"updated {len(planned)} items.")
    return 0
