#!/usr/bin/env python3
"""Gated migration: move the 11 OPS tables from KB (gsa_gateway.db) → OPS (gsa_gateway_ops.db).

Usage:
  python3 scripts/split_ops_migrate.py [--db KB_PATH] [--ops-db OPS_PATH] [--commit]
                                        [--backups-dir DIR]

Dry-run by default — prints the plan + projected per-table counts + checksums + drop list.
Pass --commit to execute: takes a hardened_backup of KB, copies all 11 MOVED tables to OPS,
runs Phase-5 back-fills, passes the acceptance gate, then drops MOVED tables from KB.

IMMORTAL-SAFE: NEVER drops any KB table before the OPS copy is count- AND checksum-verified
AND the full acceptance gate passes. Any gate failure → abort, drop nothing, nonzero exit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import (
    create_knowledge_schema,
    create_ops_schema,
    get_ops_connection,
)
from v2.core.publishing.event_projection import event_natural_key

# Default DB paths
KB_PATH = str(REPO / "gsa_gateway.db")
OPS_PATH = str(REPO / "gsa_gateway_ops.db")

# The 11 MOVED tables (canonical — schema.py:33-36)
MOVED_TABLES = [
    "posts",
    "post_templates",
    "post_deliveries",
    "events",
    "event_reminders",
    "judging_events",
    "judging_judges",
    "judging_presenters",
    "judging_scores",
    "judging_audience_votes",
    "judging_score_audit",
]

# FK-ordered drop sequence (children before parents).
# post_deliveries → posts; event_reminders → events + posts;
# judging_score_audit/scores/votes/presenters/judges → judging_events.
DROP_ORDER = [
    "post_deliveries",
    "event_reminders",
    "judging_score_audit",
    "judging_scores",
    "judging_audience_votes",
    "judging_presenters",
    "judging_judges",
    "judging_events",
    "posts",
    "events",
    "post_templates",
]

# Tables that receive org_slug (resolved from KB organizations)
NEEDS_ORG_SLUG = {"posts", "post_templates", "events"}
# Tables that receive ki_content (NULL on migration, back-filled from event_info)
NEEDS_KI_CONTENT = {"events"}

# Deterministic NULL sentinel for checksumming
_NULL_SENTINEL = "\x00NULL\x00"


# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_kb_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return ordered column names for *table* in *conn* via PRAGMA table_info."""
    rows = conn.execute(f"PRAGMA table_info(`{table}`)").fetchall()
    return [r[1] for r in rows]  # column 1 = name


def build_org_slug_map(kb_conn: sqlite3.Connection) -> dict[int, str]:
    """Build {org_id → slug} from KB organizations.

    Raises ValueError if any slug appears more than once (LOW-11 ambiguity gate).
    """
    rows = kb_conn.execute("SELECT id, slug FROM organizations").fetchall()
    slug_counts: dict[str, int] = {}
    id_map: dict[int, str] = {}
    for row in rows:
        org_id = int(row[0])
        slug = row[1]
        id_map[org_id] = slug
        slug_counts[slug] = slug_counts.get(slug, 0) + 1
    ambiguous = [s for s, c in slug_counts.items() if c > 1]
    if ambiguous:
        raise ValueError(
            f"Ambiguous org slugs (LOW-11): {ambiguous} — migration aborted"
        )
    return id_map


def normalize_value(v) -> str:
    """Deterministic string form of a single cell value for checksumming."""
    if v is None:
        return _NULL_SENTINEL
    return str(v)


def table_checksum(
    conn: sqlite3.Connection,
    table: str,
    cols: list[str],
) -> str:
    """sha256 over *cols* of *table*, ORDER BY id.

    Rows serialized with a fixed NULL sentinel so two identical data sets always
    produce the same digest and one changed cell always produces a different one.
    Uses ONLY the supplied *cols* — caller must exclude augmented cols (org_slug,
    ki_content) to keep KB and OPS digests comparable.
    """
    cols_str = ",".join(f"`{c}`" for c in cols)
    rows = conn.execute(f"SELECT {cols_str} FROM `{table}` ORDER BY id").fetchall()
    h = hashlib.sha256()
    for row in rows:
        row_repr = repr(tuple(normalize_value(v) for v in row))
        h.update(row_repr.encode("utf-8"))
    return h.hexdigest()


def copy_table(
    kb_conn: sqlite3.Connection,
    ops_conn: sqlite3.Connection,
    table: str,
    org_slug_map: dict[int, str],
) -> tuple[list[str], int]:
    """Copy *table* from KB → OPS with column augmentation.

    Returns (kb_cols, row_count).

    Augmentation:
    - posts, post_templates, events: add org_slug (resolved from org_id).
    - events: add ki_content = NULL (back-filled by Phase-5).

    Column names are derived from PRAGMA — never hardcoded. The INSERT specifies
    column names explicitly so physical DDL ordering in OPS is irrelevant.

    Raises ValueError if any org_id cannot be resolved to a slug.
    """
    kb_cols = get_kb_columns(kb_conn, table)

    # Build OPS insert col list = KB cols + augmented cols (appended at end).
    # Guard: if a column already exists in KB (e.g. combined test fixtures created
    # via create_all which includes the full OPS schema), do NOT add it again.
    ops_insert_cols = list(kb_cols)
    if table in NEEDS_KI_CONTENT and "ki_content" not in kb_cols:
        ops_insert_cols.append("ki_content")
    if table in NEEDS_ORG_SLUG and "org_slug" not in kb_cols:
        ops_insert_cols.append("org_slug")

    # Fetch all KB rows
    kb_cols_str = ",".join(f"`{c}`" for c in kb_cols)
    raw_rows = kb_conn.execute(
        f"SELECT {kb_cols_str} FROM `{table}` ORDER BY id"
    ).fetchall()

    placeholders = ",".join(["?"] * len(ops_insert_cols))
    ops_cols_str = ",".join(f"`{c}`" for c in ops_insert_cols)
    insert_sql = f"INSERT INTO `{table}` ({ops_cols_str}) VALUES ({placeholders})"

    count = 0
    for raw in raw_rows:
        row_dict = dict(zip(kb_cols, raw))

        # Augment ki_content (NULL; back-filled in Phase-5).
        # Only when ki_content is not already in KB cols.
        if table in NEEDS_KI_CONTENT and "ki_content" not in kb_cols:
            row_dict["ki_content"] = None

        # Augment org_slug — only when not already in KB cols.
        if table in NEEDS_ORG_SLUG and "org_slug" not in kb_cols:
            org_id_val = row_dict.get("org_id")
            if org_id_val is None:
                raise ValueError(
                    f"Table {table!r}: row id={row_dict.get('id')} has NULL org_id — "
                    "cannot resolve org_slug; migration aborted"
                )
            slug = org_slug_map.get(int(org_id_val))
            if slug is None:
                raise ValueError(
                    f"Table {table!r}: org_id={org_id_val} not found in KB organizations; "
                    "migration aborted"
                )
            row_dict["org_slug"] = slug

        ops_row = [row_dict[c] for c in ops_insert_cols]
        ops_conn.execute(insert_sql, ops_row)
        count += 1

    return kb_cols, count


def seed_events_sequence(ops_conn: sqlite3.Connection) -> None:
    """Seed sqlite_sequence for the events AUTOINCREMENT table to MAX(id).

    Without this the next auto-insert would restart from 1 and collide with
    already-copied rows.
    """
    row = ops_conn.execute("SELECT MAX(id) FROM events").fetchone()
    if row and row[0] is not None:
        ops_conn.execute(
            "INSERT OR REPLACE INTO sqlite_sequence(name, seq) VALUES('events', ?)",
            (int(row[0]),),
        )


def check_r3_invariant() -> dict:
    """Assert that create_knowledge_schema produces NONE of the 11 MOVED tables.

    Creates a temp DB, inspects its tables, then cleans up. Returns
    {'status': 'PASS'} or {'status': 'FAIL', 'tables_in_knowledge': [...]}
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_path = f.name
    try:
        conn = create_knowledge_schema(tmp_path)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        moved_in_knowledge = [t for t in MOVED_TABLES if t in tables]
        if moved_in_knowledge:
            return {"status": "FAIL", "tables_in_knowledge": moved_in_knowledge}
        return {"status": "PASS"}
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def acceptance_gate(
    kb_conn: sqlite3.Connection,
    ops_conn: sqlite3.Connection,
    kb_cols_map: dict[str, list[str]],
    org_slug_map: dict[int, str],
) -> dict:
    """Run all acceptance checks. Returns {'passed': bool, 'checks': dict}.

    Fails closed: any single check failure sets passed=False. No check is skipped.
    The caller MUST abort without dropping any KB table if passed=False.

    Checks performed:
    1. Per-table count: KB count == OPS count.
    2. Per-table checksum: sha256 over COMMON (KB) cols identical.
    3. Slug resolution: all org_slug values in posts/post_templates/events are non-NULL.
    4. OPS FK integrity: PRAGMA foreign_key_check returns no violations.
    5. R3 invariant: create_knowledge_schema produces NONE of the 11 MOVED tables.
    """
    checks: dict[str, dict] = {}
    passed = True

    # ── 1 & 2: per-table count + checksum ─────────────────────────────────────
    for table in MOVED_TABLES:
        kb_count = kb_conn.execute(
            f"SELECT COUNT(*) FROM `{table}`"
        ).fetchone()[0]

        try:
            ops_count = ops_conn.execute(
                f"SELECT COUNT(*) FROM `{table}`"
            ).fetchone()[0]
        except sqlite3.OperationalError as exc:
            checks[f"{table}_count"] = {
                "status": "FAIL",
                "reason": f"table missing in OPS: {exc}",
            }
            passed = False
            continue

        if kb_count != ops_count:
            checks[f"{table}_count"] = {
                "status": "FAIL",
                "kb": kb_count,
                "ops": ops_count,
                "diff": ops_count - kb_count,
            }
            passed = False
        else:
            checks[f"{table}_count"] = {"status": "PASS", "count": kb_count}

        # Checksum over KB cols only (excludes org_slug, ki_content)
        kb_cols = kb_cols_map[table]
        try:
            kb_chk = table_checksum(kb_conn, table, kb_cols)
            ops_chk = table_checksum(ops_conn, table, kb_cols)
        except sqlite3.OperationalError as exc:
            checks[f"{table}_checksum"] = {
                "status": "FAIL",
                "reason": str(exc),
            }
            passed = False
            continue

        if kb_chk != ops_chk:
            checks[f"{table}_checksum"] = {
                "status": "FAIL",
                "kb": kb_chk,
                "ops": ops_chk,
            }
            passed = False
        else:
            checks[f"{table}_checksum"] = {
                "status": "PASS",
                "digest": kb_chk,
            }

    # ── 3: slug resolution ────────────────────────────────────────────────────
    for table in ("posts", "post_templates", "events"):
        try:
            unresolved = [
                r[0]
                for r in ops_conn.execute(
                    f"SELECT id FROM `{table}` WHERE org_slug IS NULL OR org_slug=''"
                ).fetchall()
            ]
        except sqlite3.OperationalError:
            unresolved = []  # table missing — already caught above
        if unresolved:
            checks[f"{table}_slug_resolved"] = {
                "status": "FAIL",
                "unresolved_row_ids": unresolved,
            }
            passed = False
        else:
            checks[f"{table}_slug_resolved"] = {"status": "PASS"}

    # ── 4: OPS FK integrity ───────────────────────────────────────────────────
    try:
        fk_violations = ops_conn.execute("PRAGMA foreign_key_check").fetchall()
    except sqlite3.OperationalError as exc:
        checks["ops_fk_check"] = {"status": "FAIL", "reason": str(exc)}
        passed = False
        fk_violations = []

    if fk_violations:
        checks["ops_fk_check"] = {
            "status": "FAIL",
            "violations": [list(v) for v in fk_violations],
        }
        passed = False
    elif "ops_fk_check" not in checks:
        checks["ops_fk_check"] = {"status": "PASS"}

    # ── 5: R3 invariant ───────────────────────────────────────────────────────
    r3 = check_r3_invariant()
    checks["r3_invariant"] = r3
    if r3["status"] != "PASS":
        passed = False

    return {"passed": passed, "checks": checks}


# ─────────────────────────────────────────────────────────────────────────────
# Phase-5 back-fills
# ─────────────────────────────────────────────────────────────────────────────

def backfill_event_info_natural_key(kb_conn: sqlite3.Connection) -> int:
    """Recompute metadata.natural_key for existing event_info KB items.

    Uses event_natural_key(title, metadata.date, metadata.time) — the same
    3-arg formula as Phase 3. Idempotent: rows that already have the correct
    natural_key are skipped.

    Returns the number of rows updated.
    """
    rows = kb_conn.execute(
        "SELECT id, title, metadata FROM knowledge_items "
        "WHERE type='event_info' AND is_active=1"
    ).fetchall()

    updated = 0
    for row in rows:
        row_id = row[0]
        name = row[1] or ""
        try:
            meta = json.loads(row[2] or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}

        date = meta.get("date", "")
        time_val = meta.get("time", "TBD") or "TBD"

        if not name or not date:
            continue

        nk = event_natural_key(name, date, time_val)
        if meta.get("natural_key") == nk:
            continue  # already correct

        meta["natural_key"] = nk
        kb_conn.execute(
            "UPDATE knowledge_items "
            "SET metadata=?, updated_at=datetime('now') WHERE id=?",
            (json.dumps(meta), row_id),
        )
        updated += 1

    return updated


def backfill_ki_content(
    kb_conn: sqlite3.Connection,
    ops_conn: sqlite3.Connection,
) -> int:
    """Copy event_info.content → matching OPS events.ki_content.

    Match by metadata.ops_event_id (primary) or metadata.event_id (MED-8 fallback).
    Only updates rows where ki_content is currently NULL or empty.

    Returns the number of OPS event rows updated.
    """
    rows = kb_conn.execute(
        "SELECT id, content, metadata FROM knowledge_items "
        "WHERE type='event_info' AND is_active=1"
    ).fetchall()

    updated = 0
    for row in rows:
        content = row[1]
        if not content:
            continue
        try:
            meta = json.loads(row[2] or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}

        ops_event_id = meta.get("ops_event_id") or meta.get("event_id")
        if ops_event_id is None:
            continue

        result = ops_conn.execute(
            "UPDATE events "
            "SET ki_content=? "
            "WHERE id=? AND (ki_content IS NULL OR ki_content='')",
            (content, int(ops_event_id)),
        )
        if result.rowcount > 0:
            updated += 1

    return updated


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Split-ops migration: move 11 MOVED tables from KB → OPS DB."
    )
    parser.add_argument("--db", default=KB_PATH, help="KB (source) database path")
    parser.add_argument("--ops-db", default=OPS_PATH, help="OPS (destination) database path")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Execute the migration (dry-run by default)",
    )
    parser.add_argument(
        "--backups-dir",
        default=None,
        help="Override backup directory (default: .backups/ in repo root)",
    )
    args = parser.parse_args(argv)

    kb_path = args.db
    ops_path = args.ops_db
    commit = args.commit
    backups_dir = args.backups_dir

    # ── Open KB connection ────────────────────────────────────────────────────
    kb_conn = sqlite3.connect(kb_path)
    kb_conn.row_factory = sqlite3.Row

    # ── Build org_slug map (gate: ambiguous slug fails fast) ──────────────────
    try:
        org_slug_map = build_org_slug_map(kb_conn)
    except ValueError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        kb_conn.close()
        return 1

    # ── Introspect KB columns for all MOVED tables ────────────────────────────
    kb_cols_map: dict[str, list[str]] = {}
    for table in MOVED_TABLES:
        cols = get_kb_columns(kb_conn, table)
        if not cols:
            print(
                f"FATAL: Table {table!r} not found in KB {kb_path}",
                file=sys.stderr,
            )
            kb_conn.close()
            return 1
        kb_cols_map[table] = cols

    # ── Print plan header ─────────────────────────────────────────────────────
    print("=" * 64)
    print("SPLIT-OPS MIGRATION")
    print("=" * 64)
    print(f"  Source  (KB):  {kb_path}")
    print(f"  Target  (OPS): {ops_path}")
    print(f"  Mode:          {'COMMIT' if commit else 'DRY-RUN (pass --commit to write)'}")
    print()

    print("Planned table migration (KB → OPS):")
    for table in MOVED_TABLES:
        count = kb_conn.execute(f"SELECT COUNT(*) FROM `{table}`").fetchone()[0]
        chk = table_checksum(kb_conn, table, kb_cols_map[table])
        print(f"  {table:30s}  rows={count:6d}  sha256={chk[:16]}...")

    print()
    print("FK-ordered drop sequence (after gate passes):")
    for i, table in enumerate(DROP_ORDER, 1):
        print(f"  {i:2d}. DROP TABLE {table}")

    print()
    print("Rollback recipe:")
    print("  cp <backup_path> <kb_path>   (then restart services)")
    print("  # Backup path printed after --commit run")

    if not commit:
        print()
        print("[DRY-RUN] Nothing written. Pass --commit to execute.")
        kb_conn.close()
        return 0

    # ═════════════════════════════════════════════════════════════════════════
    # COMMIT MODE
    # ═════════════════════════════════════════════════════════════════════════

    # 1. Hardened backup of KB (BEFORE any write — rollback reference)
    print()
    print("Step 1: Hardened backup of KB...")
    backup_path = hardened_backup(
        kb_path, "pre-split-ops-migrate", backups_dir=backups_dir
    )
    print(f"  Backup: {backup_path}")
    print(f"  Rollback: cp {backup_path} {kb_path}")

    # 2. Build OPS schema
    print()
    print("Step 2: Building OPS schema...")
    ops_conn = create_ops_schema(ops_path)
    print(f"  OPS schema ready: {ops_path}")

    # 3. Copy all 11 MOVED tables KB → OPS (column-mapped)
    print()
    print("Step 3: Copying tables KB → OPS...")
    copied_kb_cols: dict[str, list[str]] = {}
    for table in MOVED_TABLES:
        try:
            kb_cols, count = copy_table(kb_conn, ops_conn, table, org_slug_map)
            copied_kb_cols[table] = kb_cols
            print(f"  {table:30s}  {count:6d} rows")
        except (ValueError, sqlite3.Error) as exc:
            print(f"FATAL: Error copying {table!r}: {exc}", file=sys.stderr)
            ops_conn.rollback()
            ops_conn.close()
            kb_conn.close()
            return 1

    # Seed sqlite_sequence for events AUTOINCREMENT
    seed_events_sequence(ops_conn)
    ops_conn.commit()

    # 4. Phase-5 back-fills (BEFORE the gate)
    print()
    print("Step 4: Phase-5 back-fills...")
    nk_updated = backfill_event_info_natural_key(kb_conn)
    ki_updated = backfill_ki_content(kb_conn, ops_conn)
    kb_conn.commit()
    ops_conn.commit()
    print(f"  natural_key back-filled: {nk_updated} KB event_info rows")
    print(f"  ki_content  back-filled: {ki_updated} OPS event rows")

    # 5. Acceptance gate (fail-closed)
    print()
    print("Step 5: Acceptance gate...")
    gate_result = acceptance_gate(kb_conn, ops_conn, copied_kb_cols, org_slug_map)

    for check_name, check_val in gate_result["checks"].items():
        status = check_val["status"]
        marker = "PASS" if status == "PASS" else "FAIL"
        print(f"  [{marker}] {check_name}")
        if status != "PASS":
            for k, v in check_val.items():
                if k != "status":
                    print(f"       {k}: {v}")

    if not gate_result["passed"]:
        print()
        print("GATE FAILED — aborting. Nothing dropped from KB.")
        print(f"Both DBs are inspectable:")
        print(f"  KB  (unchanged): {kb_path}")
        print(f"  OPS (incomplete): {ops_path}")
        print(f"  Backup (pre-migration): {backup_path}")
        ops_conn.close()
        kb_conn.close()
        return 1

    print("  → Gate PASSED. Proceeding to drop MOVED tables from KB.")

    # 6. Drop MOVED tables from KB (FK-ordered, LAST — immortal-safe)
    print()
    print("Step 6: Dropping MOVED tables from KB (FK-ordered)...")
    for table in DROP_ORDER:
        kb_conn.execute(f"DROP TABLE IF EXISTS `{table}`")
        print(f"  Dropped {table}")
    kb_conn.commit()

    # Final summary
    print()
    print("=" * 64)
    print("Migration COMPLETE")
    print(f"  OPS DB: {ops_path}")
    print(f"  Backup for rollback: {backup_path}")
    print(f"  Rollback command: cp {backup_path} {kb_path}")
    print("=" * 64)

    ops_conn.close()
    kb_conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
