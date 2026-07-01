"""Day-1 PROSE rebuild runner (gated).

Wipes crawl-sourced PROSE and rebuilds it fresh through the ONE canonical write path (upsert_prose),
so the corpus is one canonical row per NJIT page. PRESERVES people (crawler rows with entity_id), the
KG (nodes/edges), and manual (dashboard/scholar/migration) content — all asserted unchanged.

ALWAYS run on a DEV COPY first, then the content-aware coverage gate (scripts/prose_rebuild_gate.py),
then owner sign-off, then the atomic swap. Dry-run default; --commit writes (hardened_backup first).

  python scripts/rebuild_prose.py --db /tmp/dev_rebuild.db --commit --embed

Spec: docs/superpowers/specs/2026-06-30-day1-prose-rebuild-design.md §2/§3
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time

logger = logging.getLogger(__name__)
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database import vector_gc
from v2.core.ingestion.prose_store import ensure_prose_unique_index

# The crawl PROSE sources that get wiped + rebuilt (crawler PERSON prose is preserved via entity_id).
_WIPE_SOURCES = ("njit_www_crawl", "college_crawl", "catalog_crawl")
_WIPE_WHERE = (
    "created_by IN ('njit_www_crawl','college_crawl','catalog_crawl') "
    "OR (created_by='crawler' AND json_extract(metadata,'$.entity_id') IS NULL)"
)


def _preserve_counts(conn) -> dict:
    q = lambda s: conn.execute(s).fetchone()[0]  # noqa: E731
    return {
        "people": q("SELECT COUNT(*) FROM knowledge_items WHERE created_by='crawler' "
                    "AND json_extract(metadata,'$.entity_id') IS NOT NULL"),
        "dashboard": q("SELECT COUNT(*) FROM knowledge_items WHERE created_by='dashboard'"),
        "scholar": q("SELECT COUNT(*) FROM knowledge_items WHERE created_by='scholar'"),
        "migration": q("SELECT COUNT(*) FROM knowledge_items WHERE created_by='migration'"),
        "nodes": q("SELECT COUNT(*) FROM nodes"),
        "edges": q("SELECT COUNT(*) FROM edges"),
    }


def wipe_prose(conn) -> dict:
    """DELETE crawl-sourced prose (the _WIPE_WHERE scope), then GC orphan vectors. Asserts the
    PRESERVE set (people/KG/manual) is byte-for-byte unchanged. Does NOT commit (caller owns txn)."""
    before = _preserve_counts(conn)
    n = conn.execute(f"SELECT COUNT(*) FROM knowledge_items WHERE {_WIPE_WHERE}").fetchone()[0]
    conn.execute(f"DELETE FROM knowledge_items WHERE {_WIPE_WHERE}")
    swept = {"item_vectors": 0, "chunk_rows": 0, "chunk_vectors": 0}
    try:                                      # best-effort GC (no-op if vec0 tables absent, e.g. tests)
        swept["item_vectors"] = vector_gc.sweep_orphan_item_vectors(conn)
        swept["chunk_rows"] = vector_gc.sweep_orphan_chunk_rows(conn)
        swept["chunk_vectors"] = vector_gc.sweep_orphan_chunk_vectors(conn)
    except Exception as e:  # noqa: BLE001
        swept["error"] = str(e)
    after = _preserve_counts(conn)
    if before != after:
        raise AssertionError(f"wipe_prose touched PRESERVE data: before={before} after={after}")
    return {"wiped": n, "swept": swept, "preserve": after}


def _rebuild_catalog(conn, fetch, fetch_bytes, *, limit=0) -> dict:
    """catalog.njit.edu prose through the canonical write path (mirrors crawl_catalog.py, canonical)."""
    from v2.core.ingestion.catalog_crawl import catalog_seed_urls, iter_catalog_groups
    from v2.core.ingestion.college_crawl import ingest_college, ingest_pdf_pages
    urls = catalog_seed_urls(fetch_bytes)
    if limit:
        urls = urls[:limit]
    seen_canon: set = set()
    totals = {"prose_inserted": 0, "prose_updated": 0, "prose_unchanged": 0,
              "pdf_inserted": 0, "pdf_updated": 0, "pdf_unchanged": 0}
    for slug, name, parent, otype, res in iter_catalog_groups(urls, fetch):
        out = ingest_college(conn, slug, name, parent, res, res.html_by_url, org_type=otype,
                             created_by="catalog_crawl", canonical=True, seen_canon=seen_canon)
        for k in ("prose_inserted", "prose_updated", "prose_unchanged"):
            totals[k] += out[k]
        pdf_items = [(u, t) for p in res.prose for u, t in p.files if u.lower().endswith(".pdf")]
        if pdf_items:
            pout = ingest_pdf_pages(conn, slug, name, parent, pdf_items, fetch_bytes, org_type=otype,
                                    created_by="catalog_crawl", canonical=True, seen_canon=seen_canon)
            for k in ("pdf_inserted", "pdf_updated", "pdf_unchanged"):
                totals[k] += pout[k]
    return totals


def dfs_supplement(conn, fetch, fetch_bytes, *, entries=None, seen_canon=None,
                   max_depth=4, budget=400, delay=0.0, limit=0) -> dict:
    """DFS-crawl each entry point (college/dept subdomains + www office SECTION seeds) through the
    canonical write path to recover pages NO sitemap lists (DFS-only). Deduped against the sitemap
    sweep by the shared canonical index (a page already written comes back 'unchanged', never doubled).
    Math course-syllabus PDFs are skipped inside ingest_pdf_pages. Does NOT commit (caller owns txn).

    ``limit`` (dev): when >0, caps the per-entry page ``budget`` to make a dev subset run quick
    (mirrors the www/catalog url-limit intent). Each entry is ISOLATED — one entry that raises is
    recorded in ``failed_entries`` and the rest still run; the caller must fail the gate/report if
    ``failed_entries`` is non-empty (a failed entry ⇒ its pages surface as gate 'missing')."""
    from v2.core.ingestion.college_crawl import (
        PROSE_ENTRY_POINTS, SECTION_ENTRY_POINTS, extract_entry, ingest_college, ingest_pdf_pages)
    if entries is None:
        entries = list(PROSE_ENTRY_POINTS) + list(SECTION_ENTRY_POINTS)
    if seen_canon is None:
        seen_canon = set()
    if limit:
        budget = min(budget, limit)
    totals = {"prose_inserted": 0, "prose_updated": 0, "prose_unchanged": 0,
              "pdf_inserted": 0, "pdf_updated": 0, "pdf_unchanged": 0, "entries": 0,
              "truncated": 0, "failed_entries": []}
    for e in entries:
        try:
            res = extract_entry(e.seed, fetch, max_depth=max_depth, budget=budget, delay=delay)
            out = ingest_college(conn, e.org_slug, e.org_name, e.parent_slug, res, res.html_by_url,
                                 org_type=e.org_type, created_by="college_crawl",
                                 canonical=True, seen_canon=seen_canon)
            for k in ("prose_inserted", "prose_updated", "prose_unchanged"):
                totals[k] += out[k]
            pdf_items = [(u, t) for p in res.prose for u, t in p.files if u.lower().endswith(".pdf")]
            if pdf_items:
                pout = ingest_pdf_pages(conn, e.org_slug, e.org_name, e.parent_slug, pdf_items,
                                        fetch_bytes, org_type=e.org_type, created_by="college_crawl",
                                        canonical=True, seen_canon=seen_canon)
                for k in ("pdf_inserted", "pdf_updated", "pdf_unchanged"):
                    totals[k] += pout[k]
            totals["entries"] += 1
            totals["truncated"] += int(res.truncated)
        except Exception as exc:  # noqa: BLE001 — isolate a bad seed; do NOT abort the whole rebuild
            logger.exception("dfs_supplement: entry %s failed", e.seed)
            totals["failed_entries"].append(f"{e.seed} ({exc.__class__.__name__}: {exc})")
    return totals


def rebuild(conn, fetch, fetch_bytes, *, limit=0, supplement=True) -> dict:
    """Wipe crawl prose, then re-crawl fresh through the canonical write path: www all-hosts sitemap
    sweep + catalog + (default) the DFS coverage SUPPLEMENT (dfs_supplement) that recovers DFS-only
    pages no sitemap lists — the lose-nothing safeguard the coverage gate enforces. One canonical index
    dedups all three, so a page seen twice is written once. Then enforce the unique index."""
    from v2.core.ingestion import www_crawl as W

    from v2.core.ingestion.college_crawl import (
        SINGLETON_HTML, SINGLETON_PDFS, ingest_pdf_pages)

    wiped = wipe_prose(conn)
    www = W.run(conn, fetch, fetch_bytes, canonical=True, limit=limit)
    cat = _rebuild_catalog(conn, fetch, fetch_bytes, limit=limit)
    # The supplement dedups against www/catalog rows GLOBALLY via upsert_prose (natural_key=canonical,
    # not created_by-scoped) → a page in a sitemap AND found by DFS ends as one 'unchanged' row.
    sup = dfs_supplement(conn, fetch, fetch_bytes, delay=0.0, limit=limit) if supplement else {}
    # Keep-everything (owner 2026-07-01): recover the homepage + deep/orphaned pages no link reaches as
    # exact SINGLETONS (max_depth=0 = seed page only; a '/' DFS would walk the whole site). Same dedup
    # path, so a no-op if a sitemap already had it. PDFs go through ingest_pdf_pages directly.
    if supplement:
        singout = dfs_supplement(conn, fetch, fetch_bytes, entries=SINGLETON_HTML, max_depth=0,
                                 budget=1, delay=0.0)
        for k, v in singout.items():                       # fold HTML-singleton counts into sup
            sup[k] = sup.get(k, [] if isinstance(v, list) else 0) + v
        seen_pdf: set = set()
        for url, slug, name, parent in SINGLETON_PDFS:
            pout = ingest_pdf_pages(conn, slug, name, parent, [(url, "")], fetch_bytes,
                                    org_type="department", created_by="college_crawl",
                                    canonical=True, seen_canon=seen_pdf)
            for k in ("pdf_inserted", "pdf_updated", "pdf_unchanged"):
                sup[k] = sup.get(k, 0) + pout[k]
    ensure_prose_unique_index(conn)
    return {"wiped": wiped, "www": www["totals"], "catalog": cat, "supplement": sup}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Day-1 PROSE rebuild (wipe + canonical re-crawl)")
    ap.add_argument("--db", default="gsa_gateway.db")
    ap.add_argument("--commit", action="store_true", help="Write DB (hardened_backup first)")
    ap.add_argument("--embed", action="store_true", help="Run embed_all + embed_chunks after commit")
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--limit", type=int, default=0, help="Dev: first N urls per entry")
    args = ap.parse_args(argv)

    from v2.core.database.schema import get_connection
    from v2.core.ingestion.web_crawler import make_fetcher, make_bytes_fetcher

    if args.commit:
        from scripts._area_tag_migrate import hardened_backup
        hardened_backup(args.db, label="prose-rebuild")

    conn = get_connection(args.db)
    fetch, fetch_bytes = make_fetcher(), make_bytes_fetcher()

    def _delayed(u):
        h = fetch(u)
        if args.delay:
            time.sleep(args.delay)
        return h

    out = rebuild(conn, _delayed, fetch_bytes, limit=args.limit)
    print("wiped:", out["wiped"]["wiped"], "swept:", out["wiped"]["swept"])
    print("www totals:", out["www"])
    print("catalog totals:", out["catalog"])
    sup = out.get("supplement") or {}
    print("supplement totals:", sup)
    if sup.get("truncated"):
        print(f"  ⚠️  {sup['truncated']} entry subtree(s) hit the page budget (truncated) — "
              f"the coverage gate will flag any pages lost to truncation as MISSING.")
    if sup.get("failed_entries"):
        print(f"  ⚠️  {len(sup['failed_entries'])} supplement entr(ies) FAILED (fail the gate, "
              f"do NOT swap): {sup['failed_entries']}")
    print("preserve:", out["wiped"]["preserve"])

    if args.commit:
        conn.commit()
        print("\nCOMMITTED (run scripts/prose_rebuild_gate.py before any swap)")
        if args.embed:
            subprocess.run([sys.executable, str(REPO / "v2/scripts/embed_all.py"), args.db], check=True)
            subprocess.run([sys.executable, str(REPO / "v2/scripts/embed_chunks.py"), "--db", args.db],
                           check=True)
    else:
        print("\nDRY RUN — no commit")
    return out


if __name__ == "__main__":
    main()
