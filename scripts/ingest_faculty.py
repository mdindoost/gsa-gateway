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


def _llm_call(system: str, user: str) -> str:
    """Synchronous Ollama /api/generate call for overview generation. Grounded,
    low-temperature; returns '' on any failure (overview is optional, never fatal)."""
    import json
    import os
    import urllib.request

    base = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
    payload = json.dumps({
        "model": model, "system": system, "prompt": user, "stream": False,
        # deterministic: same facts -> same overview, so an unchanged page does not
        # churn a new version/embedding on every refresh.
        "options": {"temperature": 0, "seed": 42, "top_p": 1,
                    "num_ctx": 8192, "num_predict": 300},
    }).encode("utf-8")
    req = urllib.request.Request(f"{base}/api/generate", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read()).get("response", "")
    except Exception as exc:  # noqa: BLE001
        print(f"  {C_OFF}! overview LLM call failed: {exc}")
        return ""


def _llm_json(system: str, user: str) -> str:
    """Ollama call constrained to JSON output, for grounded web fact extraction."""
    import json
    import os
    import urllib.request

    base = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
    payload = json.dumps({
        "model": model, "system": system, "prompt": user, "stream": False,
        "format": "json",
        "options": {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 1800},
    }).encode("utf-8")
    req = urllib.request.Request(f"{base}/api/generate", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read()).get("response", "")
    except Exception as exc:  # noqa: BLE001
        print(f"  {C_OFF}! extract LLM call failed: {exc}")
        return ""


WEB_TIME_BUDGET = 240  # wall-clock seconds per professor for crawl+extract


def _web_enrich(rec, items):
    """Crawl the professor's own site (if any), extract grounded facts, merge them
    into ``items`` by natural_key. Returns (merged_items, summary_str). Bounded by a
    per-professor wall-clock budget so one large/slow site can't stall the batch."""
    from v2.core.ingestion.web_crawler import crawl_site, make_fetcher
    from v2.core.ingestion.web_extract import extract_page
    from v2.core.ingestion.web_merge import facts_to_items, merge

    site = rec.links.get("website")
    if not site:
        return items, "no personal site"
    start = time.monotonic()
    res = crawl_site(site, make_fetcher(), max_depth=2, budget=12, delay=0.5)
    facts, stopped = [], ""
    for p in res.pages:
        if time.monotonic() - start > WEB_TIME_BUDGET:
            stopped = f" (time budget {WEB_TIME_BUDGET}s hit — partial)"
            break
        facts += extract_page(p.text, rec.name, p.url, _llm_json)
    subject = f"{rec.name} ({rec.org})" if rec.org else rec.name
    web_items = facts_to_items(facts, rec.entity_id, subject)
    merged = merge(items, web_items)
    added = len(merged) - len(items)
    return merged, (f"{len(res.pages)} pages, {len(facts)} grounded facts → +{added} new items"
                    f", {len(res.recorded_files)} PDFs recorded{stopped}")


def discover(limit: int, faculty_list: str = FACULTY_LIST) -> list[str]:
    html = fetch(faculty_list)
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


def _auto_backup(db_path: str, keep: int = 10) -> None:
    """Mandatory, un-skippable backup before any write: an integrity-checked
    snapshot via the SQLite online-backup API (safe with the live bot's WAL open).
    Rotates: keeps the last ``keep`` auto-backups. Aborts the run if it fails —
    we never write without a good backup."""
    import sqlite3
    bdir = REPO / ".backups"
    bdir.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = bdir / f"gsa_gateway.{ts}.auto.db"
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
    autos = sorted(bdir.glob("gsa_gateway.*.auto.db"))
    for old in autos[:-keep]:
        old.unlink(missing_ok=True)
    print(f"  {C_OK}✓ backup{C_OFF} {dst.name} ({dst.stat().st_size:,} bytes, integrity ok)")


def commit(items_by_entity, db_path, org_id_override, changes_log,
           default_org_id=None) -> None:
    from v2.core.database.schema import get_connection
    from v2.core.ingestion.reconcile import reconcile_entity
    from v2.scripts.embed_all import _store_vector, embed_document, normalize

    _auto_backup(db_path)   # un-skippable — no write without a verified backup

    def _embed_one(text):
        vec = embed_document(text) or embed_document(text)  # retry once (Ollama flaps)
        return normalize(vec) if vec else None

    conn = get_connection(db_path)
    any_embed_fail = False
    try:
        for rec, items in items_by_entity:
            org_id = org_id_override or _resolve_org_id(conn, rec.org) or default_org_id
            if not org_id:
                print(f"  {C_OFF}! skip {rec.name}: could not resolve org_id for "
                      f"{rec.org!r} (pass --org-id / --default-org-id)")
                continue
            res = reconcile_entity(conn, org_id, rec.entity_id, items)
            # retire any legacy row for this same profile that the new pipeline did
            # NOT produce (the old crawler's monolithic type=contact card) so the
            # entity is represented only by the decomposed items.
            retired = _retire_legacy(conn, rec.source_url, rec.entity_id)
            # drop vectors for superseded/removed/retired; (re)embed the new/changed.
            # An active row with no vector is invisible to semantic search, so report
            # any embedding failure loudly.
            for iid in res.vectors_to_drop + retired:
                conn.execute("DELETE FROM knowledge_vectors WHERE item_id=?", (iid,))
            embedded = failed = 0
            for iid in res.to_embed:
                row = conn.execute(
                    "SELECT search_text FROM knowledge_items WHERE id=?", (iid,)).fetchone()
                vec = _embed_one(row["search_text"])
                if vec:
                    _store_vector(conn, iid, vec)
                    embedded += 1
                else:
                    failed += 1
            conn.commit()
            logged = _record_changes(conn, rec, res, retired, changes_log)
            note = "" if not failed else f"  {C_OFF}⚠ {failed} EMBED FAILED"
            ret = f", retired {len(retired)} legacy" if retired else ""
            chg = f"  → changes logged to {changes_log}" if logged else "  (no changes)"
            any_embed_fail = any_embed_fail or bool(failed)
            print(f"  {C_OK}✓ {rec.name}{C_OFF}: {res.summary()}{ret}  "
                  f"(org_id={org_id}, embedded {embedded}/{len(res.to_embed)}){note}{C_DIM}{chg}{C_OFF}")
    finally:
        conn.close()
    if any_embed_fail:
        print(f"\n  {C_OFF}⚠ Some items were committed WITHOUT an embedding (Ollama). "
              f"They are keyword-searchable but not semantic. Backfill with:\n"
              f"      python v2/scripts/embed_all.py        # resumable: embeds only the missing")


def _retire_legacy(conn, source_url: str, entity_id: str) -> list[int]:
    """Deactivate active rows for this profile URL that the new pipeline did NOT
    produce (legacy cards have no / a different metadata.entity_id). Returns their
    ids so the caller can drop their vectors."""
    rows = conn.execute(
        "SELECT id FROM knowledge_items WHERE source_url=? AND is_active=1 "
        "AND COALESCE(json_extract(metadata,'$.entity_id'),'') <> ?",
        (source_url, entity_id)).fetchall()
    ids = [r["id"] for r in rows]
    for iid in ids:
        conn.execute(
            "UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') WHERE id=?",
            (iid,))
    return ids


def _record_changes(conn, rec, res, retired, changes_log) -> bool:
    """Append a human-readable diff for this entity to ``changes_log`` so a future
    re-crawl shows exactly what was added / updated / removed / retired. Returns
    True if anything changed (an identical re-crawl writes nothing)."""
    if not (res.inserted_ids or res.superseded or res.deactivated_ids or retired):
        return False

    def title(iid):
        r = conn.execute("SELECT type,title FROM knowledge_items WHERE id=?", (iid,)).fetchone()
        return f"{r['type']}: {r['title']}" if r else f"#{iid}"

    import datetime
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    out = [f"\n[{ts}] {rec.name}  ({rec.entity_id})  {res.summary()}"]
    out += [f"  + added    {title(i)}" for i in res.inserted_ids]
    out += [f"  ~ updated  {title(new)}" for _old, new in res.superseded]
    out += [f"  - removed  {title(i)}" for i in res.deactivated_ids]
    out += [f"  ⊘ retired  {title(i)}" for i in retired]
    Path(changes_log).parent.mkdir(parents=True, exist_ok=True)
    with open(changes_log, "a", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    return True


# Adapter org labels -> the org slug in the organizations table.
_ORG_ALIASES = {"ying wu college of computing": "ywcc"}


def _resolve_org_id(conn, org_label: str):
    """Resolve a page's department label to an org id: exact slug, then exact name,
    then a type-constrained LIKE (shortest = most specific). Exact-first avoids the
    OR/LIMIT pitfall where 'Computer Science' could bind to 'Computer Science & Eng'."""
    if not org_label or not org_label.strip():
        return None
    low = org_label.strip().lower()
    slug = _ORG_ALIASES.get(low, low.replace(" ", "-"))
    for sql, param in (
        ("SELECT id FROM organizations WHERE lower(slug)=? LIMIT 1", slug),
        ("SELECT id FROM organizations WHERE lower(name)=? LIMIT 1", low),
        ("SELECT id FROM organizations WHERE type IN ('department','college') "
         "AND lower(name) LIKE ? ORDER BY length(name) LIMIT 1", f"%{low}%"),
    ):
        row = conn.execute(sql, (param,)).fetchone()
        if row:
            return row["id"]
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", action="append", help="profile URL (repeatable)")
    src.add_argument("--limit", type=int, help="crawl first N from the department faculty list")
    ap.add_argument("--department", default="cs",
                    help="department key from the registry (cs, ds, informatics). "
                         "Sets the faculty list + fallback org. Default: cs")
    ap.add_argument("--commit", action="store_true",
                    help="WRITE to the database (default: dry run, no writes)")
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"), help="database path")
    ap.add_argument("--org-id", type=int, help="force this org_id for all profiles")
    ap.add_argument("--default-org-id", type=int,
                    help="fallback org_id used only when a page's dept can't be resolved")
    ap.add_argument("--changes-log", default=str(REPO / "logs" / "ingest_changes.log"),
                    help="append a per-entity diff here on --commit (re-crawl audit trail)")
    ap.add_argument("--overview", action="store_true",
                    help="generate a grounded narrative overview per profile (local LLM)")
    ap.add_argument("--web", action="store_true",
                    help="also crawl each professor's own site and merge grounded facts")
    args = ap.parse_args()

    from v2.core.ingestion.departments import get as get_dept
    dept = get_dept(args.department)
    # registry supplies the fallback org unless the user forced one
    if args.default_org_id is None:
        args.default_org_id = dept.default_org_id

    if args.url:
        urls = args.url
    else:
        if dept.discovery != "static":
            raise SystemExit(
                f"{dept.name} faculty list ({dept.faculty_list}) is '{dept.discovery}'-"
                f"rendered — static discovery won't find profiles. {dept.note}")
        urls = discover(args.limit, dept.faculty_list)
    print(f"{C_DIM}Dept: {dept.name} (org {dept.default_org_id})  |  "
          f"Mode: {'COMMIT (writes to DB)' if args.commit else 'DRY RUN (no writes)'}"
          f"  |  {len(urls)} profile(s){C_OFF}")

    parsed, total_items = [], 0
    for i, url in enumerate(urls, 1):
        try:
            rec = parse_entity(url, fetch(url))
        except Exception as exc:  # noqa: BLE001 - one bad profile shouldn't abort the run
            print(f"\n  ! {url}: fetch/parse failed: {exc}")
            continue
        if args.overview:
            from v2.core.ingestion.overview import generate as gen_overview
            rec.overview = gen_overview(rec, _llm_call)
        items = decompose(rec)
        if args.web:
            try:
                items, web_summary = _web_enrich(rec, items)
                print(f"   {C_DIM}web: {web_summary}{C_OFF}")
            except Exception as exc:  # noqa: BLE001 - web is best-effort; never abort the batch
                print(f"   {C_OFF}! web enrich failed for {rec.name}: {exc} (NJIT items kept)")
        total_items += len(items)
        parsed.append((rec, items))
        show(rec, items)
        if len(urls) > 1:
            time.sleep(1)  # be polite to the server

    print(f"\n{C_DIM}{'─' * 60}{C_OFF}")
    print(f"Parsed {len(parsed)} profile(s) → {total_items} items total.")
    if args.commit:
        print("Committing…")
        commit(parsed, args.db, args.org_id, args.changes_log, args.default_org_id)
    else:
        print(f"{C_DIM}Dry run — nothing written. Re-run with --commit to persist.{C_OFF}")


if __name__ == "__main__":
    main()
