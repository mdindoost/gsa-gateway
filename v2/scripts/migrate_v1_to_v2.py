"""GSA Gateway v1 -> v2 migration (Step 2).

Additive and idempotent. Creates v2 tables, seeds the NJIT org hierarchy,
imports all v1 knowledge content into ``knowledge_items``, backfills ``org_id``
on the v1 tables, and seeds default settings + post templates. It does NOT
generate embeddings (that is Step 3, ``embed_all.py``) and never deletes or
rewrites v1 data.

Safety model:
  * Develop/validate against a copy (``gsa_gateway_v2dev.db``).
  * ``--dry-run`` runs the FULL migration against a throwaway temp copy of the
    target and prints the exact report, touching nothing real.
  * When the target IS the live ``gsa_gateway.db``, a timestamped backup is
    created automatically and cannot be skipped — not even with ``--yes``.

Usage:
    python v2/scripts/migrate_v1_to_v2.py gsa_gateway_v2dev.db --dry-run
    python v2/scripts/migrate_v1_to_v2.py gsa_gateway_v2dev.db
    python v2/scripts/migrate_v1_to_v2.py gsa_gateway.db --yes   # live (auto-backup)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import yaml

# Allow running as a plain script (python v2/scripts/migrate_v1_to_v2.py).
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.database.schema import create_all  # noqa: E402

DATA_DIR = REPO_ROOT / "bot" / "data"
LIVE_DB_NAME = "gsa_gateway.db"

# Which v1 tables receive an org_id column + backfill.
V1_TABLES = [
    "questions",
    "initiatives",
    "feedback",
    "admin_actions",
    "response_feedback",
    "events_log",
    "conversation_stats",
    "events",
]

# File -> (node key, knowledge type, parser). node key resolved to an org id.
QA_FILES = [
    ("gsa_faq.md", "gsa", "faq"),
    ("bot_features.md", "gsa", "faq"),
    ("mmi_workshop.md", "mmi", "faq"),
]
SECTION_FILES = [
    ("travel_award.md", "gsa", "policy"),
    ("gsa_constitution.md", "gsa", "policy"),
    ("club_finance.md", "gsa", "policy"),
    ("rules.md", "gsa", "policy"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Parsers
# ─────────────────────────────────────────────────────────────────────────────

def parse_qa(text: str) -> list[tuple[str, str]]:
    """Parse ``## Q: ...`` / ``**A:** ...`` blocks into (question, answer)."""
    items: list[tuple[str, str]] = []
    blocks = re.split(r"(?m)^##\s+Q:\s*", text)[1:]  # drop preamble before first Q
    for block in blocks:
        lines = block.splitlines()
        question = lines[0].strip()
        body = "\n".join(lines[1:])
        # stop at the next heading if any slipped through
        body = re.split(r"(?m)^##\s+", body)[0]
        answer = re.sub(r"^\s*\*\*A:\*\*\s*", "", body.strip())
        if question and answer:
            items.append((question, answer.strip()))
    return items


def parse_sections(text: str) -> list[tuple[str, str]]:
    """Parse ``## Title`` + body sections (skips the leading H1/preamble)."""
    items: list[tuple[str, str]] = []
    blocks = re.split(r"(?m)^##\s+", text)[1:]
    for block in blocks:
        lines = block.splitlines()
        title = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        if title and body:
            items.append((title, body))
    return items


def fmt_contact(c: dict) -> tuple[str, str, dict]:
    """Return (title, plain-text content, metadata) for a contact entry."""
    title = c.get("role") or c.get("name") or "Contact"
    lines = []
    if c.get("role"):
        lines.append(c["role"])
    if c.get("name"):
        lines.append(c["name"])
    if c.get("email"):
        lines.append(f"Email: {c['email']}")
    if c.get("office"):
        lines.append(f"Office: {c['office']}")
    if c.get("hours"):
        lines.append(f"Hours: {c['hours']}")
    if c.get("phone"):
        lines.append(f"Phone: {c['phone']}")
    if c.get("notes"):
        lines.append(c["notes"])
    meta = {k: v for k, v in c.items() if v is not None}
    return title, "\n".join(lines), meta


# ─────────────────────────────────────────────────────────────────────────────
# Backup
# ─────────────────────────────────────────────────────────────────────────────

def backup_before_migrate(db_path: str) -> str:
    """Create a timestamped, WAL-consistent backup. Cannot be skipped on live."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.backup_{timestamp}"
    shutil.copy2(db_path, backup_path)
    # Copy WAL/SHM sidecars too so the snapshot includes un-checkpointed writes
    # from the running bot. Restore = copy the set back (or just the .db once
    # the WAL has been checkpointed).
    for ext in ("-wal", "-shm"):
        side = db_path + ext
        if os.path.exists(side):
            shutil.copy2(side, backup_path + ext)
    print(f"  Backup created: {backup_path}")
    return backup_path


def is_live_db(db_path: str) -> bool:
    return os.path.basename(db_path) == LIVE_DB_NAME


# ─────────────────────────────────────────────────────────────────────────────
# Migration steps (each idempotent)
# ─────────────────────────────────────────────────────────────────────────────

def add_org_id_columns(conn: sqlite3.Connection) -> list[str]:
    altered = []
    for table in V1_TABLES:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "org_id" not in cols:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN org_id INTEGER REFERENCES organizations(id)"
            )
            altered.append(table)
    return altered


def get_or_create_org(conn, name, slug, otype, parent_id, description=None, metadata=None):
    row = conn.execute("SELECT id FROM organizations WHERE slug=?", (slug,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO organizations(parent_id,name,slug,type,description,metadata) "
        "VALUES (?,?,?,?,?,?)",
        (parent_id, name, slug, otype, description, json.dumps(metadata or {})),
    )
    return cur.lastrowid


def seed_org_hierarchy(conn) -> dict[str, int]:
    njit = get_or_create_org(
        conn, "New Jersey Institute of Technology", "njit", "university", None,
        "NJIT — root organization node.",
    )
    gsa = get_or_create_org(
        conn, "Graduate Student Association", "gsa", "gsa", njit,
        "NJIT Graduate Student Association.",
    )
    mmi = get_or_create_org(
        conn, "Multimedia Intelligence Workshop", "mmi", "event_series", njit,
        "Annual NJIT Workshop on Multimedia Intelligence",
        {"website": "gsanjit.com/mmi2026", "organizer": "gsa-vpa@njit.edu", "edition": "2nd"},
    )
    return {"njit": njit, "gsa": gsa, "mmi": mmi}


def backfill_org_id(conn, gsa_id: int) -> dict[str, int]:
    counts = {}
    for table in V1_TABLES:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        cur = conn.execute(
            f"UPDATE {table} SET org_id=? WHERE org_id IS NULL", (gsa_id,)
        )
        counts[table] = cur.rowcount
    return counts


def _item_exists(conn, org_id, ktype, title) -> bool:
    return conn.execute(
        "SELECT 1 FROM knowledge_items WHERE org_id=? AND type=? AND title=? AND is_active=1",
        (org_id, ktype, title),
    ).fetchone() is not None


def _insert_item(conn, org_id, ktype, title, content, metadata=None, source_url=None):
    if _item_exists(conn, org_id, ktype, title):
        return False
    conn.execute(
        "INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,created_by) "
        "VALUES (?,?,?,?,?,?,?)",
        (org_id, ktype, title, content, json.dumps(metadata or {}), source_url, "migration"),
    )
    return True


def migrate_knowledge(conn, orgs: dict[str, int]) -> dict[str, int]:
    counts: dict[str, int] = {}

    def bump(key, n=1):
        counts[key] = counts.get(key, 0) + n

    # Markdown Q&A files
    for fname, node, ktype in QA_FILES:
        path = DATA_DIR / fname
        if not path.exists():
            continue
        for q, a in parse_qa(path.read_text(encoding="utf-8")):
            if _insert_item(conn, orgs[node], ktype, q, a, {"source_file": fname}):
                bump(f"{node}:{ktype}")

    # Markdown section files
    for fname, node, ktype in SECTION_FILES:
        path = DATA_DIR / fname
        if not path.exists():
            continue
        for title, body in parse_sections(path.read_text(encoding="utf-8")):
            if _insert_item(conn, orgs[node], ktype, title, body, {"source_file": fname}):
                bump(f"{node}:{ktype}")

    # contacts.yml -> GSA contacts
    cpath = DATA_DIR / "contacts.yml"
    if cpath.exists():
        data = yaml.safe_load(cpath.read_text(encoding="utf-8")) or {}
        for key, c in (data.get("contacts") or {}).items():
            if not isinstance(c, dict):
                continue
            title, content, meta = fmt_contact(c)
            meta["key"] = key
            if _insert_item(conn, orgs["gsa"], "contact", title, content, meta):
                bump("gsa:contact")

    # resources.yml -> GSA resources (flatten categories)
    rpath = DATA_DIR / "resources.yml"
    if rpath.exists():
        data = yaml.safe_load(rpath.read_text(encoding="utf-8")) or {}
        for category, items in (data.get("resources") or {}).items():
            for r in items or []:
                title = r.get("title", "Resource")
                url = r.get("url", "")
                desc = r.get("description", "")
                content = desc + (f"\nLink: {url}" if url else "")
                meta = {"category": category, "url": url}
                if _insert_item(conn, orgs["gsa"], "resource", title, content, meta, url):
                    bump("gsa:resource")

    return counts


def migrate_events(conn, gsa_id: int) -> dict:
    """Import events.yml into the events table, then create event_info items."""
    report = {"yml_imported": 0, "event_info_created": 0, "rows": []}

    # 1. Import events.yml rows into the events table if missing (by name+date).
    #    Track the set of yml-sourced events so ONLY those become knowledge_items
    #    (pre-existing rows like the "Test Coffee Hour" stay in the table only).
    yml_keys: set[tuple] = set()
    epath = DATA_DIR / "events.yml"
    if epath.exists():
        data = yaml.safe_load(epath.read_text(encoding="utf-8")) or {}
        for e in (data.get("events") or []):
            yml_keys.add((e.get("name"), e.get("date")))
            dup = conn.execute(
                "SELECT 1 FROM events WHERE name=? AND date=?", (e.get("name"), e.get("date"))
            ).fetchone()
            if dup:
                continue
            conn.execute(
                "INSERT INTO events(name,date,time,location,description,organizer,"
                "rsvp_link,category,created_at,created_by,org_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    e.get("name"), e.get("date"), e.get("time", "TBD"),
                    e.get("location", "TBD"), e.get("description", ""),
                    e.get("organizer", "GSA"), e.get("rsvp_link", ""),
                    e.get("category", "general"),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "migration", gsa_id,
                ),
            )
            report["yml_imported"] += 1

    # 2. event_info knowledge_items — ONLY for events.yml-sourced events.
    for row in conn.execute("SELECT * FROM events"):
        from_yml = (row["name"], row["date"]) in yml_keys
        if from_yml:
            title = row["name"]
            bits = [f"{row['name']} — {row['date']} at {row['time']}, {row['location']}."]
            if row["description"]:
                bits.append(row["description"])
            if row["rsvp_link"]:
                bits.append(f"RSVP: {row['rsvp_link']}")
            content = "\n".join(bits)
            meta = {
                "event_id": row["id"], "date": row["date"], "time": row["time"],
                "location": row["location"], "category": row["category"],
                "organizer": row["organizer"], "rsvp_link": row["rsvp_link"],
            }
            if _insert_item(conn, gsa_id, "event_info", title, content, meta):
                report["event_info_created"] += 1
        report["rows"].append((row["id"], row["name"], row["date"], row["category"], from_yml))
    return report


# University-wide behavioral defaults — seeded on the NJIT ROOT node so every
# child (GSA, MMI, future colleges) inherits them unless it overrides locally.
ROOT_SETTINGS = [
    ("signature.default", "_NJIT Graduate Student Association_", "string", "Default signature"),
    ("signature.variables", json.dumps({
        "org_name": "NJIT Graduate Student Association",
        "short_name": "NJIT GSA",
        "website": "gsanjit.com",
        "discord": "discord.gg/RbRMTFNTQD",
        "telegram": "t.me/GSAGateWayNJIT",
    }), "json", "Signature template variables"),
    ("org.timezone", "America/New_York", "string", "Timezone"),
    ("default.platforms", json.dumps(["discord", "telegram"]), "json", "Default platforms"),
    ("default.send_time", "09:00", "string", "Default send time"),
    ("default.auto_delete_hours", "24", "int", "Auto-delete window (hours, 1-48) when a post opts in"),
    ("reminders.default", json.dumps([
        {"offset": 7, "unit": "days", "channels": ["discord", "telegram"]},
        {"offset": 1, "unit": "days", "channels": ["discord", "telegram"]},
        {"offset": 1, "unit": "hours", "channels": ["discord"]},
    ]), "json", "Default event reminder schedule"),
    ("retriever.event_boost", "1.2", "string", "Multiply RRF score of event_info items"),
    ("retriever.pool_size", "40", "int", "Fusion candidate pool per leg (>= MIN_POOL_SIZE)"),
]

# GSA-specific settings — identity, channels and feature flags stay on the GSA node.
GSA_SETTINGS = [
    ("org.name", "NJIT Graduate Student Association", "string", "Full org name"),
    ("org.short_name", "NJIT GSA", "string", "Short name"),
    ("org.website", "gsanjit.com", "string", "Website"),
    ("org.discord_invite", "discord.gg/RbRMTFNTQD", "string", "Discord invite"),
    ("org.telegram_channel", "@GSAGateWayNJIT", "string", "Telegram channel"),
    ("org.office", "Campus Center 110A", "string", "Office location"),
    ("org.office_hours", "Weekdays 11AM-5PM", "string", "Office hours"),
    ("org.email", "gsa-pres@njit.edu", "string", "Contact email"),
    ("default.channel.announcement", "gsa-announcements", "string", None),
    ("default.channel.event", "gsa-events", "string", None),
    ("default.channel.mathcafe", "gsa-mathcafe", "string", None),
    ("default.channel.worldcup", "world-cup-2026", "string", None),
    ("default.channel.broadcast", "gsa-announcements", "string", None),
    ("feature.mathcafe", "true", "bool", None),
    ("feature.worldcup", "true", "bool", None),
    ("feature.feedback_buttons", "true", "bool", None),
    ("feature.transcription", "false", "bool", None),
    ("feature.knowledge_graph", "false", "bool", None),
]

# Keys that belong on the root node (used by the 007 move for already-migrated dbs).
ROOT_KEYS = [k for k, *_ in ROOT_SETTINGS]


def _has_migration(conn, version: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version=?", (version,)
    ).fetchone() is not None


def seed_settings(conn, njit_id: int, gsa_id: int) -> int:
    inserted = 0
    for org_id, rows in ((njit_id, ROOT_SETTINGS), (gsa_id, GSA_SETTINGS)):
        for key, value, vtype, desc in rows:
            cur = conn.execute(
                "INSERT OR IGNORE INTO settings(org_id,key,value,type,description,updated_by) "
                "VALUES (?,?,?,?,?,?)",
                (org_id, key, value, vtype, desc, "migration"),
            )
            inserted += cur.rowcount
    return inserted


def move_root_settings(conn, njit_id: int, gsa_id: int) -> int:
    """Migration 007: relocate university-wide defaults from GSA to the NJIT root.

    Idempotent and guarded by schema_migrations. For databases migrated before the
    root/GSA split (e.g. the live db), this moves the ROOT_KEYS up so the whole
    org tree inherits them. A key already present on the root wins; the stale GSA
    copy is dropped.
    """
    if _has_migration(conn, "007_move_root_settings"):
        return 0
    moved = 0
    for key in ROOT_KEYS:
        on_gsa = conn.execute(
            "SELECT 1 FROM settings WHERE org_id=? AND key=?", (gsa_id, key)
        ).fetchone()
        if not on_gsa:
            continue
        on_root = conn.execute(
            "SELECT 1 FROM settings WHERE org_id=? AND key=?", (njit_id, key)
        ).fetchone()
        if on_root:
            conn.execute("DELETE FROM settings WHERE org_id=? AND key=?", (gsa_id, key))
        else:
            conn.execute("UPDATE settings SET org_id=? WHERE org_id=? AND key=?",
                         (njit_id, gsa_id, key))
        moved += 1
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES ('007_move_root_settings')")
    return moved


def seed_templates(conn, gsa_id: int) -> int:
    templates = [
        ("MathCafe Daily", "mathcafe",
         json.dumps({"freq": "daily", "time": "09:00", "timezone": "America/New_York"}),
         "gsa-mathcafe",
         "Daily MathCafe fact. Fact selection is handled by the MathCafe service."),
        ("World Cup Tracker", "worldcup",
         json.dumps({"freq": "event_driven", "note": "Posts driven by the World Cup tracker service."}),
         "world-cup-2026",
         "Live World Cup 2026 match notifications."),
    ]
    inserted = 0
    for name, ptype, recurrence, channel, content in templates:
        exists = conn.execute(
            "SELECT 1 FROM post_templates WHERE org_id=? AND name=?", (gsa_id, name)
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO post_templates(org_id,name,content,post_type,recurrence,"
            "channels,discord_channel,created_by) VALUES (?,?,?,?,?,?,?,?)",
            (gsa_id, name, content, ptype, recurrence,
             json.dumps(["discord", "telegram"]), channel, "migration"),
        )
        inserted += 1
    return inserted


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration + reporting
# ─────────────────────────────────────────────────────────────────────────────

def _v1_counts(conn) -> dict[str, int]:
    out = {}
    for t in V1_TABLES:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)
        ).fetchone()
        if row:
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    return out


def _fts_smoke(conn) -> list[tuple[str, str]]:
    queries = ["travel award", "club penalty", "MMI 2026", "GSA president", "office hours"]
    results = []
    for q in queries:
        row = conn.execute(
            "SELECT ki.type, ki.title FROM knowledge_fts "
            "JOIN knowledge_items ki ON ki.id=knowledge_fts.rowid "
            "WHERE knowledge_fts MATCH ? AND ki.is_active=1 "
            "ORDER BY bm25(knowledge_fts) LIMIT 1",
            (" OR ".join(q.split()),),
        ).fetchone()
        results.append((q, f"[{row['type']}] {row['title']}" if row else "(no hit)"))
    return results


def _migrate(db_path: str) -> dict:
    conn = create_all(db_path)  # v2 tables (FK on, sqlite-vec loaded), committed
    try:
        report = {"db": db_path}
        report["v1_before"] = _v1_counts(conn)
        report["altered"] = add_org_id_columns(conn)
        orgs = seed_org_hierarchy(conn)
        report["orgs"] = orgs
        report["backfilled"] = backfill_org_id(conn, orgs["gsa"])
        report["knowledge"] = migrate_knowledge(conn, orgs)
        report["events"] = migrate_events(conn, orgs["gsa"])
        report["settings_inserted"] = seed_settings(conn, orgs["njit"], orgs["gsa"])
        report["settings_moved"] = move_root_settings(conn, orgs["njit"], orgs["gsa"])
        report["templates_inserted"] = seed_templates(conn, orgs["gsa"])
        conn.commit()
        report["v1_after"] = _v1_counts(conn)
        report["ki_total"] = conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
        report["ki_by_type"] = dict(conn.execute(
            "SELECT type, COUNT(*) FROM knowledge_items GROUP BY type"
        ).fetchall())
        report["ki_by_org"] = dict(conn.execute(
            "SELECT o.slug, COUNT(*) FROM knowledge_items ki "
            "JOIN organizations o ON o.id=ki.org_id GROUP BY o.slug"
        ).fetchall())
        report["fts"] = _fts_smoke(conn)
        report["settings_by_org"] = dict(conn.execute(
            "SELECT o.slug, COUNT(*) FROM settings s JOIN organizations o ON o.id=s.org_id "
            "GROUP BY o.slug"
        ).fetchall())
        report["events_table_in_schema"] = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'"
        ).fetchone() is not None
        return report
    finally:
        conn.close()


def print_report(r: dict, dry_run: bool) -> None:
    banner = "DRY RUN — nothing written" if dry_run else "MIGRATION COMPLETE"
    print("\n" + "=" * 60)
    print(f"  {banner}")
    print("=" * 60)

    print("\nOrganization hierarchy:")
    print(f"  NJIT (id={r['orgs']['njit']})")
    print(f"  ├── GSA (id={r['orgs']['gsa']})")
    print(f"  └── MMI (id={r['orgs']['mmi']}, event_series)")

    print(f"\nv1 tables: org_id added to {r['altered'] or '(already present)'}")
    print("v1 row counts (only `events` may grow — by the events.yml import):")
    yml_imported = r["events"]["yml_imported"]
    for t, before in r["v1_before"].items():
        after = r["v1_after"].get(t, before)
        expected = before + (yml_imported if t == "events" else 0)
        flag = "OK" if after == expected else "!! UNEXPECTED"
        delta = f" (+{yml_imported} from events.yml)" if t == "events" and yml_imported else ""
        bf = r["backfilled"].get(t, 0)
        print(f"  {t:<20} {before:>4} -> {after:>4}{delta}  (backfilled: {bf})  {flag}")

    print(f"\nknowledge_items total: {r['ki_total']}")
    print("  by org:  " + "  ".join(f"{slug}={n}" for slug, n in sorted(r["ki_by_org"].items())))
    for ktype, n in sorted(r["ki_by_type"].items()):
        print(f"  {ktype:<14} {n}")

    ev = r["events"]
    print(f"\nEvents: imported {ev['yml_imported']} from events.yml, "
          f"created {ev['event_info_created']} event_info items")
    for eid, name, date, cat, from_yml in ev["rows"]:
        note = ("  -> event_info created" if from_yml
                else "  -> skipped KB (stays in events table only)")
        print(f"  id={eid} {date} [{cat}] {name}{note}")

    print(f"\nSettings inserted: {r['settings_inserted']} | moved to NJIT root (007): {r.get('settings_moved', 0)}")
    by = r.get("settings_by_org", {})
    print("  settings by org: " + "  ".join(f"{slug}={n}" for slug, n in sorted(by.items()))
          + "   (university-wide defaults on njit root; GSA-specific on gsa)")
    print(f"events table present in schema: {'yes' if r.get('events_table_in_schema') else 'NO'}")
    print(f"Post templates inserted: {r['templates_inserted']}")

    print("\nFTS5 keyword smoke test (vector RAG comes in Step 4):")
    for q, hit in r["fts"]:
        print(f"  '{q}'  ->  {hit}")
    print("=" * 60 + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Migrate GSA Gateway v1 -> v2 (additive).")
    ap.add_argument("db_path", help="Target SQLite db (use a copy until validated).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Run against a throwaway temp copy and report; write nothing.")
    ap.add_argument("--yes", action="store_true",
                    help="Skip confirmation (live backup is still mandatory).")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db_path):
        ap.error(f"Database not found: {args.db_path}")

    if args.dry_run:
        tmp = f"{args.db_path}.dryrun_{os.getpid()}"
        # WAL-consistent snapshot (the live bot may have un-checkpointed writes in
        # the WAL that a plain file copy would miss).
        src = sqlite3.connect(args.db_path)
        dst = sqlite3.connect(tmp)
        src.backup(dst)
        src.close()
        dst.close()
        try:
            print_report(_migrate(tmp), dry_run=True)
        finally:
            for p in (tmp, tmp + "-wal", tmp + "-shm"):
                if os.path.exists(p):
                    os.remove(p)
        return

    live = is_live_db(args.db_path)
    print(f"Target: {args.db_path}  ({'LIVE production db' if live else 'dev/copy'})")
    if live:
        # Mandatory, un-skippable backup before touching the live database.
        backup_before_migrate(args.db_path)
    elif not args.yes:
        if input("Proceed with migration? [y/N] ").strip().lower() != "y":
            print("Aborted.")
            return

    print_report(_migrate(args.db_path), dry_run=False)


if __name__ == "__main__":
    main()
