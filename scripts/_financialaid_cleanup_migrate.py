#!/usr/bin/env python3
"""Clean-replace the pre-crawler Financial Aid KB rows + supersede the pre-crawler people (SEPARATE gated migration).

Run ONLY after crawl_financialaid.py --commit has written + verified the new crawler rows.
Retires: every superseded njit-crawl/migration KB row, and every dashboard KB row that points at a
www.njit.edu/financialaid WEB page (any URL alias, ANY type). KEEPS: any dashboard row whose source_url is
NOT a live /financialaid page (genuinely manual) and every crawler row.

PEOPLE: Financial Aid had pre-crawler people from BOTH njit-crawl (e.g. Rebecca Wolk) and possibly dashboard.
A pre-crawler person (key NOT starting 'crawler/') SUPERSEDED by a crawler person is retired — matched
by EMAIL (the strong key). A name-only match (no email / homonym) is NOT auto-dropped; it is LISTED
under "KEPT FOR OWNER REVIEW" (honest-partial). This is a CURATION decision OUTSIDE the crawler.

Source-scoped + dry-run default + hardened backup before any write.
Spec: docs/superpowers/specs/2026-06-24-financialaid-crawl-design.md (G7)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection

FINAID_SLUG = "financialaid"

# Anchored: njit.edu/financialaid followed by '/' or end-of-string — covers /financialaid, /financialaid/contact,
# /financialaid/optional-practical-training; never a hypothetical /financialaid-foo path. Forward-safe.
_FINAID_URL = re.compile(r"njit\.edu/financialaid(/|$)", re.I)


def _finaid_org_node(conn):
    oid = conn.execute("SELECT id FROM organizations WHERE slug=?", (FINAID_SLUG,)).fetchone()[0]
    onode = conn.execute("SELECT id FROM nodes WHERE type='Org' AND "
                         "json_extract(attrs,'$.org_id')=?", (oid,)).fetchone()
    return oid, (onode[0] if onode else None)


def _norm_name(name) -> str:
    s = re.sub(r"\([^)]*\)", " ", (name or "").replace("\xa0", " "))
    return " ".join(s.lower().split())


def _email_of(attrs) -> str:
    return ((json.loads(attrs) if attrs else {}).get("email") or "").strip().lower()


def select_people(conn) -> tuple[list[dict], list[dict]]:
    """Returns (retire, keep_for_review). EMAIL is the strong identity key: a pre-crawler person
    (key NOT 'crawler/...') is auto-RETIRED only when its email matches a crawler person's email.
    A name-only / no-email match is KEPT FOR OWNER REVIEW (homonym-safe)."""
    oid, onode = _finaid_org_node(conn)
    if onode is None:
        return [], []
    crawler_emails, crawler_names = set(), set()
    for nm, attrs in conn.execute(
        "SELECT name, attrs FROM nodes WHERE type='Person' AND key LIKE 'crawler/financialaid/%'"):
        if nm:
            crawler_names.add(_norm_name(nm))
        em = _email_of(attrs)
        if em:
            crawler_emails.add(em)
    retire, review = [], []
    for pid, key, name, attrs in conn.execute(
        "SELECT p.id, p.key, p.name, p.attrs FROM edges e JOIN nodes p ON e.src_id=p.id "
        "WHERE e.dst_id=? AND e.type='has_role' AND e.is_active=1 AND p.is_active=1 "
        "AND p.type='Person' AND p.key NOT LIKE 'crawler/%'", (onode,)):
        em = _email_of(attrs)
        if em and em in crawler_emails:
            retire.append({"person_id": pid, "name": name, "key": key,
                           "reason": "superseded-by-crawler (email)"})
        else:
            note = "name also matches a crawler person" if _norm_name(name) in crawler_names \
                else "no crawler match"
            review.append({"person_id": pid, "name": name, "key": key, "email": em, "note": note})
    return retire, review


def _is_ogi_site_url(url) -> bool:
    return bool(url) and bool(_FINAID_URL.search(url))


def select_retire(conn) -> list[dict]:
    oid = conn.execute("SELECT id FROM organizations WHERE slug=?", (FINAID_SLUG,)).fetchone()[0]
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
        elif cb == "dashboard" and has_crawler and _is_ogi_site_url(url):
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
        print(f"\n  ⚠ KEPT FOR OWNER REVIEW — {len(review)} pre-crawler people NOT matched by email:")
        for p in review:
            print(f"      keep? id={p['person_id']:>6} {p['name']}  ({p['email'] or 'no-email'})  [{p['note']}]")
    print(f"\n=== {len(retire)} KB rows + {len(people)} people to retire; {len(review)} kept for review ===")

    if not args.commit:
        print("(dry run — pass --commit; a hardened backup is taken first)")
        return 0

    _, onode = _finaid_org_node(conn)
    hardened_backup(args.db, "pre-financialaid-cleanup")
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
