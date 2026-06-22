"""DB-backed entry-point registry (aspect='office' rows only — spec §3 [SE4]).
One writer per fact; the crawler reads (list_active) and writes (upsert_candidate,
mark_crawled). add_seed creates an already-active office entry point; upsert_candidate
records a discovered hub awaiting gated activation."""
from __future__ import annotations

import sqlite3


def add_seed(conn: sqlite3.Connection, *, url: str, scope_prefix: str, org_slug: str,
             parent_slug: str, org_type: str = "office",
             crawl_interval_days: int | None = None) -> int:
    row = conn.execute("SELECT id FROM crawl_entry_points WHERE url=?", (url,)).fetchone()
    if row:
        conn.execute("UPDATE crawl_entry_points SET status='active', source='seed', "
                     "scope_prefix=?, org_slug=?, parent_slug=?, org_type=?, "
                     "crawl_interval_days=? WHERE id=?",
                     (scope_prefix, org_slug, parent_slug, org_type, crawl_interval_days, row[0]))
        return row[0]
    cur = conn.execute(
        "INSERT INTO crawl_entry_points(url,scope_prefix,aspect,org_slug,parent_slug,"
        "org_type,status,source,crawl_interval_days) "
        "VALUES(?,?,'office',?,?,?,'active','seed',?)",
        (url, scope_prefix, org_slug, parent_slug, org_type, crawl_interval_days))
    return cur.lastrowid


def upsert_candidate(conn: sqlite3.Connection, *, url: str, discovered_from_url: str) -> int:
    row = conn.execute("SELECT id FROM crawl_entry_points WHERE url=?", (url,)).fetchone()
    if row:
        return row[0]                          # idempotent: never downgrade an existing row
    cur = conn.execute(
        "INSERT INTO crawl_entry_points(url,aspect,status,source,discovered_from_url) "
        "VALUES(?,'office','candidate','discovered',?)", (url, discovered_from_url))
    return cur.lastrowid


def activate(conn: sqlite3.Connection, ep_id: int) -> None:
    conn.execute("UPDATE crawl_entry_points SET status='active' WHERE id=?", (ep_id,))


def list_active(conn: sqlite3.Connection, aspect: str = "office") -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM crawl_entry_points WHERE status='active' AND aspect=? ORDER BY id",
        (aspect,)).fetchall()


def mark_crawled(conn: sqlite3.Connection, ep_id: int) -> None:
    conn.execute("UPDATE crawl_entry_points SET last_crawled_at=datetime('now') WHERE id=?",
                 (ep_id,))
