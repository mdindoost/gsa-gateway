#!/usr/bin/env python3
"""Clean-replace the pre-crawler Career Development KB rows + supersede the manual people (gated migration).

Run ONLY after crawl_career.py --commit has written + verified the new crawler rows.
Retires: every superseded njit-crawl/migration KB row, and every dashboard KB row pointing at a
www.njit.edu/dos WEB page. KEEPS: any dashboard row NOT on a /dos page (genuinely manual) + every crawler row.

PEOPLE: the DOS roster carries NO per-person email, so a pre-crawler person (key NOT 'crawler/...')
is matched by NORMALIZED NAME (the dashboard stores 'Given Surname'; the crawler reorders 'Surname,
Given' -> 'Given Surname'). A name match retires the superseded manual person; a NON-matching person
is KEPT FOR OWNER REVIEW (honest-partial; never auto-dropped). Name-match is acceptable here because
the office is tiny (7) and the crawler reproduces the exact roster; a homonym would surface in review.

Source-scoped + dry-run default + hardened backup before any write.
Spec: docs/superpowers/specs/2026-06-24-career-crawl-design.md (G7)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection

CAREER_SLUG = "career-development"

# Anchored: njit.edu/dos followed by '/' or end-of-string — covers /dos, /dos/contact.php,
# /dos/node/201; never a hypothetical /dos-foo path. Forward-safe.
_CAREER_URL = re.compile(r"njit\.edu/careerservices(/|$)", re.I)


def _career_org_node(conn):
    oid = conn.execute("SELECT id FROM organizations WHERE slug=?", (CAREER_SLUG,)).fetchone()[0]
    onode = conn.execute("SELECT id FROM nodes WHERE type='Org' AND "
                         "json_extract(attrs,'$.org_id')=?", (oid,)).fetchone()
    return oid, (onode[0] if onode else None)


def _norm_name(name) -> str:
    s = re.sub(r"\([^)]*\)", " ", (name or "").replace("\xa0", " "))
    return " ".join(s.lower().split())


def select_people(conn) -> tuple[list[dict], list[dict]]:
    """(retire, keep_for_review). DOS roster has no email -> match pre-crawler people by NORMALIZED
    NAME against the crawler people. Name match -> retire; no match -> KEPT FOR OWNER REVIEW."""
    oid, onode = _career_org_node(conn)
    if onode is None:
        return [], []
    crawler_names = {_norm_name(r[0]) for r in conn.execute(
        "SELECT name FROM nodes WHERE type='Person' AND key LIKE 'crawler/career-development/%'") if r[0]}
    retire, review = [], []
    for pid, key, name in conn.execute(
        "SELECT p.id, p.key, p.name FROM edges e JOIN nodes p ON e.src_id=p.id "
        "WHERE e.dst_id=? AND e.type='has_role' AND e.is_active=1 AND p.is_active=1 "
        "AND p.type='Person' AND p.key NOT LIKE 'crawler/%'", (onode,)):
        if _norm_name(name) in crawler_names:
            retire.append({"person_id": pid, "name": name, "key": key,
                           "reason": "superseded-by-crawler (name)"})
        else:
            review.append({"person_id": pid, "name": name, "key": key})
    return retire, review


def _is_career_site_url(url) -> bool:
    return bool(url) and bool(_CAREER_URL.search(url))


def select_retire(conn) -> list[dict]:
    oid = conn.execute("SELECT id FROM organizations WHERE slug=?", (CAREER_SLUG,)).fetchone()[0]
    has_crawler = conn.execute(
        "SELECT 1 FROM knowledge_items WHERE org_id=? AND created_by='crawler' AND is_active=1 "
        "LIMIT 1", (oid,)).fetchone() is not None
    rows = conn.execute(
        "SELECT id, created_by, title, source_url FROM knowledge_items "
        "WHERE is_active=1 AND org_id=?", (oid,)).fetchall()
    retire: list[dict] = []
    for rid, cb, title, url in rows:
        if cb == "crawler":
            continue
        if cb in ("njit-crawl", "migration"):
            retire.append({"id": rid, "created_by": cb, "title": title,
                           "source_url": url, "reason": "superseded-by-crawler"})
        elif cb == "dashboard" and has_crawler and _is_career_site_url(url):
            retire.append({"id": rid, "created_by": cb, "title": title,
                           "source_url": url, "reason": "superseded-by-crawler"})
    return retire


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true", help="deactivate the rows (else dry run)")
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    args = ap.parse_args(argv)

    conn = get_connection(args.db)
    retire = select_retire(conn)
    people, review = select_people(conn)
    shown = 0
    for r in retire:
        if shown < 30:
            print(f"  retire KB  id={r['id']:>6} [{r['created_by']}] {r['reason']:<22} {r['source_url']}")
        shown += 1
    if len(retire) > 30:
        print(f"  … and {len(retire) - 30} more KB rows")
    for p in people:
        print(f"  retire person id={p['person_id']:>6} {p['reason']:<26} {p['name']} [{p['key']}]")
    if review:
        print(f"\n  ⚠ KEPT FOR OWNER REVIEW — {len(review)} pre-crawler people NOT matched by name:")
        for p in review:
            print(f"      keep? id={p['person_id']:>6} {p['name']} [{p['key']}]")
    print(f"\n=== {len(retire)} KB rows + {len(people)} people to retire; {len(review)} kept for review ===")

    if not args.commit:
        print("(dry run — pass --commit; a hardened backup is taken first)")
        return 0

    _, onode = _career_org_node(conn)
    hardened_backup(args.db, "pre-career-cleanup")
    conn.executemany("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') WHERE id=?",
                     [(r["id"],) for r in retire])
    for p in people:
        conn.execute("UPDATE edges SET is_active=0, updated_at=datetime('now') "
                     "WHERE src_id=? AND dst_id=? AND is_active=1", (p["person_id"], onode))
        conn.execute("UPDATE nodes SET is_active=0, updated_at=datetime('now') WHERE id=?",
                     (p["person_id"],))
    conn.commit()
    print(f"RETIRED {len(retire)} KB rows + {len(people)} people. {len(review)} kept for review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
