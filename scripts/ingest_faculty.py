"""Hybrid-ingestion runner for NJIT faculty profiles (Phase 1a).

Pipeline:  fetch  ->  parse_entity (EntityRecord, uncapped)  ->  decompose (KItems)
           ->  [--commit] reconcile_entity + embed.

DEFAULT IS A DRY RUN: it fetches and shows EXACTLY what items would be created for
each profile, and writes NOTHING. Only ``--commit`` touches the database.

Examples
--------
  # dry run, one professor — show the decomposition
  python scripts/ingest_faculty.py --url https://people.njit.edu/profile/ikoutis

  # dry run, first 5 from the CS faculty list
  python scripts/ingest_faculty.py --limit 5

  # write to the DB (gated — back up first), resolving the org from the page
  python scripts/ingest_faculty.py --url https://people.njit.edu/profile/ikoutis --commit
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.decompose import decompose
from v2.core.ingestion.njit_adapter import fetch, parse_entity

FACULTY_LIST = "https://cs.njit.edu/faculty"

C_HEAD = "\033[1;36m"; C_KEY = "\033[0;33m"; C_DIM = "\033[0;90m"; C_OK = "\033[0;32m"; C_OFF = "\033[0m"


def discover(limit: int) -> list[str]:
    html = fetch(FACULTY_LIST)
    seen, out = set(), []
    for m in re.findall(r"(?:https:)?//people\.njit\.edu/profile/[A-Za-z0-9_-]+", html):
        u = "https:" + m if m.startswith("//") else m
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:limit]


def show(rec, items) -> None:
    print(f"\n{C_HEAD}═══ {rec.name or '(no name)'}{C_OFF}  "
          f"{C_DIM}{rec.entity_id}{C_OFF}")
    print(f"    org={rec.org or '?'}  verified={rec.verified}  "
          f"titles={'; '.join(rec.titles) or '—'}")
    print(f"    publications={len(rec.publications)}  teaching={len(rec.teaching)}  "
          f"service={len(rec.service)}  education={len(rec.education)}  "
          f"bio={'yes' if rec.bio else 'no'}  links={','.join(rec.links) or '—'}")
    print(f"    {C_OK}→ decomposes into {len(items)} searchable items:{C_OFF}")
    for it in items:
        extra = ""
        if it.type == "publication" and it.metadata.get("year"):
            extra = f"  {C_DIM}year={it.metadata['year']}{C_OFF}"
        print(f"      {C_KEY}[{it.type:<18}]{C_OFF} {C_DIM}{it.natural_key}{C_OFF}{extra}")
        print(f"          {it.content}")


def commit(items_by_entity, db_path, org_id_override) -> None:
    from v2.core.database.schema import get_connection
    from v2.core.ingestion.reconcile import reconcile_entity
    from v2.scripts.embed_all import _store_vector, embed_document, normalize

    conn = get_connection(db_path)
    try:
        for rec, items in items_by_entity:
            org_id = org_id_override or _resolve_org_id(conn, rec.org)
            if not org_id:
                print(f"  {C_OFF}! skip {rec.name}: could not resolve org_id for "
                      f"{rec.org!r} (pass --org-id)")
                continue
            res = reconcile_entity(conn, org_id, rec.entity_id, items)
            print(f"  {C_OK}✓ {rec.name}{C_OFF}: {res.summary()}  (org_id={org_id})")
            # embed the new/changed items; drop vectors for the superseded/removed
            for iid in res.vectors_to_drop:
                conn.execute("DELETE FROM knowledge_vectors WHERE item_id=?", (iid,))
            for iid in res.to_embed:
                row = conn.execute(
                    "SELECT search_text FROM knowledge_items WHERE id=?", (iid,)).fetchone()
                vec = normalize(embed_document(row["search_text"]))
                if vec:
                    _store_vector(conn, iid, vec)
            conn.commit()
    finally:
        conn.close()


def _resolve_org_id(conn, org_label: str):
    if not org_label:
        return None
    row = conn.execute(
        "SELECT id FROM organizations WHERE lower(name)=lower(?) "
        "OR lower(name) LIKE lower(?) LIMIT 1",
        (org_label, f"%{org_label}%")).fetchone()
    return row["id"] if row else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", action="append", help="profile URL (repeatable)")
    src.add_argument("--limit", type=int, help="crawl first N from the CS faculty list")
    ap.add_argument("--commit", action="store_true",
                    help="WRITE to the database (default: dry run, no writes)")
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"), help="database path")
    ap.add_argument("--org-id", type=int, help="force this org_id for all profiles")
    args = ap.parse_args()

    urls = args.url if args.url else discover(args.limit)
    print(f"{C_DIM}Mode: {'COMMIT (writes to DB)' if args.commit else 'DRY RUN (no writes)'}"
          f"  |  {len(urls)} profile(s){C_OFF}")

    parsed, total_items = [], 0
    for i, url in enumerate(urls, 1):
        try:
            rec = parse_entity(url, fetch(url))
        except Exception as exc:  # noqa: BLE001 - one bad profile shouldn't abort the run
            print(f"\n  ! {url}: fetch/parse failed: {exc}")
            continue
        items = decompose(rec)
        total_items += len(items)
        parsed.append((rec, items))
        show(rec, items)
        if len(urls) > 1:
            time.sleep(1)  # be polite to the server

    print(f"\n{C_DIM}{'─' * 60}{C_OFF}")
    print(f"Parsed {len(parsed)} profile(s) → {total_items} items total.")
    if args.commit:
        print("Committing…")
        commit(parsed, args.db, args.org_id)
    else:
        print(f"{C_DIM}Dry run — nothing written. Re-run with --commit to persist.{C_OFF}")


if __name__ == "__main__":
    main()
