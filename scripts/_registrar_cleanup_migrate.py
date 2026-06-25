#!/usr/bin/env python3
"""Clean-replace the pre-crawler Registrar KB rows (SEPARATE gated migration).

Run ONLY after crawl_registrar.py --commit has written + verified the new crawler rows.
Retires: every superseded njit-crawl row, and every dashboard row that points at a
www.njit.edu/registrar WEB page (any URL alias, ANY type — the existing homepage stub is
type='contact', so the retire set is built by source + URL, never by type). KEEPS: any
dashboard row whose source_url is NOT a live registrar page (genuinely manual) and every crawler
row. This is a CURATION decision — it lives OUTSIDE the crawler (hard line: the crawler brings
data only). Source-scoped + dry-run default + hardened backup before any write.

DELTA vs Bursar: the Registrar roster carries no email, so dashboard people superseded by the
crawler are matched by NAME (not email). Registrar currently has 0 pre-existing KG people, so
this is normally [] — but the name match keeps the migration correct if a manual dupe is ever
added before a recrawl. A non-matching dashboard person is LEFT for owner review (honest-partial).

Spec: docs/superpowers/specs/2026-06-24-registrar-crawl-design.md (G7)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import re

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection

REGISTRAR_SLUG = "registrar"

# Anchored: njit.edu/registrar followed by '/' or end-of-string — covers /registrar/,
# /registrar/node/455, /registrar/directory/mallstaff.php, but NEVER a hypothetical
# /registrar-foo path (forward-safety, senior-review B3 carry-over).
_REGISTRAR_URL = re.compile(r"njit\.edu/registrar(/|$)", re.I)


def _registrar_org_node(conn):
    oid = conn.execute("SELECT id FROM organizations WHERE slug=?", (REGISTRAR_SLUG,)).fetchone()[0]
    onode = conn.execute("SELECT id FROM nodes WHERE type='Org' AND "
                         "json_extract(attrs,'$.org_id')=?", (oid,)).fetchone()
    return oid, (onode[0] if onode else None)


def _norm(name) -> str:
    return " ".join((name or "").lower().split())


def select_retire_people(conn) -> list[dict]:
    """Dashboard-created Registrar staff the crawler now supersedes (NAME match — the roster has
    no email). Registrar normally has 0 pre-existing people, so this is normally []. A dashboard
    person with NO crawler name match is LEFT for the owner to review (honest-partial; never
    auto-drop a non-duplicate)."""
    oid, onode = _registrar_org_node(conn)
    if onode is None:
        return []
    crawler_names = {_norm(r[0]) for r in conn.execute(
        "SELECT name FROM nodes WHERE type='Person' AND key LIKE 'crawler/registrar/%'") if r[0]}
    retire: list[dict] = []
    for pid, name in conn.execute(
        "SELECT p.id, p.name FROM edges e JOIN nodes p ON e.src_id=p.id "
        "WHERE e.dst_id=? AND e.type='has_role' AND e.is_active=1 "
        "AND p.key LIKE 'dashboard/registrar/%'", (onode,)):
        if _norm(name) in crawler_names:
            retire.append({"person_id": pid, "name": name, "reason": "superseded-by-crawler"})
    return retire


def _is_registrar_site_url(url) -> bool:
    """True for a row that points at a Registrar WEB page (any URL alias). NULL / off-site URLs
    are NOT registrar pages."""
    return bool(url) and bool(_REGISTRAR_URL.search(url))


def select_retire(conn) -> list[dict]:
    """Rows to deactivate so Registrar ends with ONE clean crawler source:
      - every ``njit-crawl`` row (the older one-off pass, now superseded);
      - every ``migration`` row (none today; retired as superseded if present);
      - every ``dashboard`` row that points at a /registrar WEB page (any URL alias, ANY type) —
        the crawler now holds that page verbatim, so the manual stub/excerpt is superseded.
    KEPT: every ``crawler`` row, and any ``dashboard`` row that is NOT a registrar-site page
    (genuinely manual — an internal note, an off-site URL, or a NULL-URL row).
    Guard: dashboard rows are only retired once the crawl has actually run (crawler rows exist),
    so a mistaken pre-crawl run can't strip manual data. CURATION decision — OUTSIDE the crawler."""
    oid = conn.execute("SELECT id FROM organizations WHERE slug=?", (REGISTRAR_SLUG,)).fetchone()[0]
    has_crawler = conn.execute(
        "SELECT 1 FROM knowledge_items WHERE org_id=? AND created_by='crawler' AND is_active=1 "
        "LIMIT 1", (oid,)).fetchone() is not None
    rows = conn.execute(
        "SELECT id, created_by, title, source_url FROM knowledge_items "
        "WHERE is_active=1 AND org_id=?", (oid,)).fetchall()      # type-agnostic (catches the stub)
    retire: list[dict] = []
    for rid, cb, title, url in rows:
        if cb == "crawler":
            continue                                     # the new source of truth — never retired
        if cb in ("njit-crawl", "migration"):
            retire.append({"id": rid, "created_by": cb, "title": title,
                           "source_url": url, "reason": "superseded-by-crawler"})
        elif cb == "dashboard" and has_crawler and _is_registrar_site_url(url):
            retire.append({"id": rid, "created_by": cb, "title": title,
                           "source_url": url, "reason": "superseded-by-crawler"})
        # dashboard rows NOT on a crawler-covered URL (incl. NULL url) are manual-only → KEPT
    return retire


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true", help="deactivate the rows (else dry run)")
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    args = ap.parse_args(argv)

    conn = get_connection(args.db)
    retire = select_retire(conn)
    people = select_retire_people(conn)
    shown = 0
    for r in retire:
        if shown < 30:
            print(f"  retire KB  id={r['id']:>6} [{r['created_by']}] {r['reason']:<22} {r['source_url']}")
        shown += 1
    if len(retire) > 30:
        print(f"  … and {len(retire) - 30} more KB rows")
    for p in people:
        print(f"  retire person id={p['person_id']:>6} {p['reason']:<22} {p['name']}")
    print(f"=== {len(retire)} KB rows + {len(people)} dashboard people to retire ===")

    if not args.commit:
        print("(dry run — pass --commit; a hardened backup is taken first)")
        return 0

    _, onode = _registrar_org_node(conn)
    hardened_backup(args.db, "pre-registrar-cleanup")
    conn.executemany("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') WHERE id=?",
                     [(r["id"],) for r in retire])
    for p in people:                                   # deactivate the role edge + the node
        conn.execute("UPDATE edges SET is_active=0, updated_at=datetime('now') "
                     "WHERE src_id=? AND dst_id=? AND is_active=1", (p["person_id"], onode))
        conn.execute("UPDATE nodes SET is_active=0, updated_at=datetime('now') WHERE id=?",
                     (p["person_id"],))
    conn.commit()
    print(f"RETIRED {len(retire)} KB rows + {len(people)} dashboard people.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
