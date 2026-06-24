#!/usr/bin/env python3
"""Clean-replace the pre-crawler Graduate Studies KB rows (SEPARATE gated migration).

Run ONLY after crawl_gradstudies.py --commit has written + verified the new crawler rows.
Retires: the dead-subdomain migration stub, the superseded njit-crawl rows, and duplicate
dashboard rows (keeps the lowest-id one). KEEPS: any dashboard row whose source_url is NOT a
live www.njit.edu/graduatestudies page (genuinely manual) and every crawler row. This is a
CURATION decision — it lives OUTSIDE the crawler (hard line: the crawler brings data only).
Source-scoped + dry-run default + hardened backup before any write.

Spec: docs/superpowers/specs/2026-06-24-graduate-studies-crawl-design.md (G7)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection

GS_SLUG = "graduate-studies"


def _gs_org_node(conn):
    oid = conn.execute("SELECT id FROM organizations WHERE slug=?", (GS_SLUG,)).fetchone()[0]
    onode = conn.execute("SELECT id FROM nodes WHERE type='Org' AND "
                         "json_extract(attrs,'$.org_id')=?", (oid,)).fetchone()
    return oid, (onode[0] if onode else None)


def select_retire_people(conn) -> list[dict]:
    """Dashboard-created GS staff the crawler now supersedes. The crawler captures the SAME
    people from contact.php with strictly MORE data (email + phone + titles + unit vs the
    manual email-only rows), so a manual ``dashboard/graduate-studies/<slug>`` person whose
    email matches a crawler-created GS person is a duplicate → retire (deactivate the GS
    has_role edge + the node). A dashboard GS person with NO crawler email match is LEFT for
    the owner to review/remove manually (honest-partial; never auto-drop a non-duplicate)."""
    oid, onode = _gs_org_node(conn)
    if onode is None:
        return []
    crawler_emails = {r[0] for r in conn.execute(
        "SELECT json_extract(attrs,'$.email') FROM nodes "
        "WHERE type='Person' AND key LIKE 'crawler/graduate-studies/%'") if r[0]}
    retire: list[dict] = []
    for pid, name, attrs in conn.execute(
        "SELECT p.id, p.name, p.attrs FROM edges e JOIN nodes p ON e.src_id=p.id "
        "WHERE e.dst_id=? AND e.type='has_role' AND e.is_active=1 "
        "AND p.key LIKE 'dashboard/graduate-studies/%'", (onode,)):
        email = (json.loads(attrs) if attrs else {}).get("email")
        if email and email in crawler_emails:
            retire.append({"person_id": pid, "name": name, "email": email,
                           "reason": "superseded-by-crawler"})
    return retire


def _is_gs_site_url(url) -> bool:
    """True for a row that points at a Graduate Studies WEB page (any URL alias). The crawler
    walks the whole /graduatestudies/ subtree but stores each page under a single canonical URL
    (e.g. /node/101), while old rows used a different alias of the SAME page
    (/content/new-phd-credit-requirements). Matching exact URLs misses those aliases, so we match
    by the GS-site URL pattern instead — alias-proof. NULL / off-site URLs are NOT GS-site pages."""
    return bool(url) and ("njit.edu/graduatestudies" in url or "graduatestudies.njit.edu" in url)


def select_retire(conn) -> list[dict]:
    """Rows to deactivate so GSO ends with ONE clean crawler source:
      - every ``migration`` row (the dead graduatestudies.njit.edu subdomain stub);
      - every ``njit-crawl`` row (the older one-off pass, now superseded);
      - every ``dashboard`` row that points at a /graduatestudies/ WEB page (any URL alias) —
        the crawler now holds that page verbatim, so the manual paraphrase/excerpt is superseded.
    KEPT: every ``crawler`` row, and any ``dashboard`` row that is NOT a GS-site page (genuinely
    manual — an internally-authored note, an off-site URL, or a NULL-URL row).
    Guard: dashboard rows are only retired once the crawl has actually run (crawler rows exist),
    so a mistaken pre-crawl run can't strip manual data. This is a CURATION decision — it lives
    OUTSIDE the crawler (hard line)."""
    oid = conn.execute("SELECT id FROM organizations WHERE slug=?", (GS_SLUG,)).fetchone()[0]
    has_crawler = conn.execute(
        "SELECT 1 FROM knowledge_items WHERE org_id=? AND created_by='crawler' AND is_active=1 "
        "LIMIT 1", (oid,)).fetchone() is not None
    rows = conn.execute(
        "SELECT id, created_by, title, source_url FROM knowledge_items "
        "WHERE is_active=1 AND org_id=?", (oid,)).fetchall()
    retire: list[dict] = []
    for rid, cb, title, url in rows:
        if cb == "crawler":
            continue                                     # the new source of truth — never retired
        if cb == "migration":
            reason = ("dead-subdomain-stub" if url and "graduatestudies.njit.edu" in url
                      else "superseded-by-crawler")
            retire.append({"id": rid, "created_by": cb, "title": title,
                           "source_url": url, "reason": reason})
        elif cb == "njit-crawl":
            retire.append({"id": rid, "created_by": cb, "title": title,
                           "source_url": url, "reason": "superseded-by-crawler"})
        elif cb == "dashboard" and has_crawler and _is_gs_site_url(url):
            # a dashboard row on a GS web page (any alias) the crawler now holds verbatim
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
    for r in retire:
        print(f"  retire KB  id={r['id']:>6} [{r['created_by']}] {r['reason']:<24} {r['source_url']}")
    for p in people:
        print(f"  retire person id={p['person_id']:>6} {p['reason']:<22} {p['name']} ({p['email']})")
    print(f"=== {len(retire)} KB rows + {len(people)} dashboard people to retire ===")

    if not args.commit:
        print("(dry run — pass --commit; a hardened backup is taken first)")
        return 0

    _, onode = _gs_org_node(conn)
    hardened_backup(args.db, "pre-gradstudies-cleanup")
    conn.executemany("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') WHERE id=?",
                     [(r["id"],) for r in retire])
    for p in people:                                   # deactivate the GS role edge + the node
        conn.execute("UPDATE edges SET is_active=0, updated_at=datetime('now') "
                     "WHERE src_id=? AND dst_id=? AND is_active=1", (p["person_id"], onode))
        conn.execute("UPDATE nodes SET is_active=0, updated_at=datetime('now') WHERE id=?",
                     (p["person_id"],))
    conn.commit()
    print(f"RETIRED {len(retire)} KB rows + {len(people)} dashboard people.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
