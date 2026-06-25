#!/usr/bin/env python3
"""Clean-replace the pre-crawler Admissions KB rows + supersede the manual people (SEPARATE gated migration).

Run ONLY after crawl_admissions.py --commit has written + verified the new crawler rows.
Retires: every superseded njit-crawl/migration row, and every dashboard row that points at a
www.njit.edu/admissions WEB page (any URL alias, ANY type). KEEPS: any dashboard row whose
source_url is NOT a live admissions page (genuinely manual) and every crawler row. The 26
management.njit.edu MTSM admission-requirement rows are a DIFFERENT office (not under org 21 and
not matched by the /admissions URL regex) — never touched.

PEOPLE: the office team was hand-authored on the dashboard (id 333–358, batch 2026-06-17). The
crawl now reproduces it from the live site (the source of truth, owner decision), so a dashboard
person SUPERSEDED by a crawler person (matched by EMAIL, else by parenthetical-normalized NAME —
the crawler carries nicknames the manual rows dropped) is retired. A dashboard person with NO
crawler match is NOT dropped — it is LISTED under "KEPT FOR OWNER REVIEW" (honest-partial; owner
decides retire-vs-keep on the leftovers).

This is a CURATION decision — it lives OUTSIDE the crawler (hard line: the crawler brings data
only). Source-scoped + dry-run default + hardened backup before any write.

Spec: docs/superpowers/specs/2026-06-24-admissions-crawl-design.md (G7)
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

ADMISSIONS_SLUG = "graduate-admissions"

# Anchored: njit.edu/admissions followed by '/' or end-of-string — covers /admissions/,
# /admissions/contact-admissions, /admissions/graduate/graduateadvisors.php; NEVER matches
# management.njit.edu/admission-requirements ("admission-" != "admissions"), forward-safe.
_ADMISSIONS_URL = re.compile(r"njit\.edu/admissions(/|$)", re.I)


def _admissions_org_node(conn):
    oid = conn.execute("SELECT id FROM organizations WHERE slug=?", (ADMISSIONS_SLUG,)).fetchone()[0]
    onode = conn.execute("SELECT id FROM nodes WHERE type='Org' AND "
                         "json_extract(attrs,'$.org_id')=?", (oid,)).fetchone()
    return oid, (onode[0] if onode else None)


def _norm_name(name) -> str:
    """Lowercase, drop parenthetical nicknames + nbsp/whitespace runs so the crawler's
    'Yenitza (Jenny) Ruiz' matches the manual 'Yenitza Ruiz'."""
    s = re.sub(r"\([^)]*\)", " ", (name or "").replace("\xa0", " "))
    return " ".join(s.lower().split())


def _email_of(attrs) -> str:
    return ((json.loads(attrs) if attrs else {}).get("email") or "").strip().lower()


def select_people(conn) -> tuple[list[dict], list[dict]]:
    """Returns (retire, keep_for_review). EMAIL is the strong identity key: a dashboard person is
    auto-RETIRED only when its email matches a crawler person's email. A name-only match (no email,
    or a homonym) is NOT auto-dropped — it is KEPT FOR OWNER REVIEW (the review note records whether
    the normalized name collides with a crawler name, so the owner has the context). This avoids the
    homonym false-positive of name-matching (two 'Christina McGuire's, nickname-stripping collisions)."""
    oid, onode = _admissions_org_node(conn)
    if onode is None:
        return [], []
    crawler_emails, crawler_names = set(), set()
    for nm, attrs in conn.execute(
        "SELECT name, attrs FROM nodes WHERE type='Person' AND key LIKE 'crawler/graduate-admissions/%'"):
        if nm:
            crawler_names.add(_norm_name(nm))
        em = _email_of(attrs)
        if em:
            crawler_emails.add(em)
    retire, review = [], []
    for pid, name, attrs in conn.execute(
        "SELECT p.id, p.name, p.attrs FROM edges e JOIN nodes p ON e.src_id=p.id "
        "WHERE e.dst_id=? AND e.type='has_role' AND e.is_active=1 "
        "AND p.key LIKE 'dashboard/graduate-admissions/%'", (onode,)):
        em = _email_of(attrs)
        if em and em in crawler_emails:
            retire.append({"person_id": pid, "name": name, "reason": "superseded-by-crawler (email)"})
        else:
            note = "name also matches a crawler person" if _norm_name(name) in crawler_names \
                else "no crawler match"
            review.append({"person_id": pid, "name": name, "email": em, "note": note})
    return retire, review


def _is_admissions_site_url(url) -> bool:
    return bool(url) and bool(_ADMISSIONS_URL.search(url))


def select_retire(conn) -> list[dict]:
    """KB rows to deactivate so Admissions ends with ONE clean crawler source (see module docstring)."""
    oid = conn.execute("SELECT id FROM organizations WHERE slug=?", (ADMISSIONS_SLUG,)).fetchone()[0]
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
        elif cb == "dashboard" and has_crawler and _is_admissions_site_url(url):
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
    for r in retire:
        print(f"  retire KB  id={r['id']:>6} [{r['created_by']}] {r['reason']:<22} {r['source_url']}")
    for p in people:
        print(f"  retire person id={p['person_id']:>6} {p['reason']:<22} {p['name']}")
    if review:
        print(f"\n  ⚠ KEPT FOR OWNER REVIEW — {len(review)} dashboard people NOT on the live site "
              f"(not auto-dropped):")
        for p in review:
            print(f"      keep? id={p['person_id']:>6} {p['name']}  ({p['email'] or 'no-email'})  [{p['note']}]")
    print(f"\n=== {len(retire)} KB rows + {len(people)} dashboard people to retire; "
          f"{len(review)} kept for review ===")

    if not args.commit:
        print("(dry run — pass --commit; a hardened backup is taken first)")
        return 0

    _, onode = _admissions_org_node(conn)
    hardened_backup(args.db, "pre-admissions-cleanup")
    conn.executemany("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') WHERE id=?",
                     [(r["id"],) for r in retire])
    for p in people:
        conn.execute("UPDATE edges SET is_active=0, updated_at=datetime('now') "
                     "WHERE src_id=? AND dst_id=? AND is_active=1", (p["person_id"], onode))
        conn.execute("UPDATE nodes SET is_active=0, updated_at=datetime('now') WHERE id=?",
                     (p["person_id"],))
    conn.commit()
    print(f"RETIRED {len(retire)} KB rows + {len(people)} dashboard people. "
          f"{len(review)} kept for owner review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
