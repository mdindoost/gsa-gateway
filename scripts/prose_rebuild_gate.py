"""Content-aware, fail-closed coverage gate for the day-1 prose rebuild (Task 8).

The load-bearing lose-nothing safeguard (spec §5.1, rev2-SE#1/#2, rev2-RAG#2). After the wipe the
fresh crawl is the ONLY copy, so a URL-presence check is NOT enough — a page that came back truncated
would pass a set-membership test while silently losing content. This gate therefore, for every prose
URL in the pre-wipe BACKUP:
  1. canonicalizes BOTH sides through `canonical_prose_url` (backup keys were written under the OLD
     per-engine normalizers — a raw compare false-FAILs on trailing-slash/alias), then
  2. requires the rebuilt DB to COVER the URL (⊇, minus a reviewed drop-list), AND
  3. requires the rebuilt row's real-content length ≥ the backup's (minus a small tolerance).
Plus: PRESERVE counts (people/KG/manual) byte-identical, and ≤1 active prose row per canonical URL.
Any violation → ok=False (fail-closed). Run on the DEV copy before any atomic swap.

  python scripts/prose_rebuild_gate.py --rebuilt /tmp/dev_rebuild.db --backup .backups/<pre>.db

Spec: docs/superpowers/specs/2026-06-30-day1-prose-rebuild-design.md §5.1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import hashlib
import re

from v2.core.ingestion.canonical_url import canonical_prose_url
from v2.core.ingestion.prose_quality import prose_quality_len


def _fingerprint(content: str):
    """Whitespace-normalized content hash — stable across a URL rename (same page, new slug)."""
    norm = re.sub(r"\s+", " ", content or "").strip().lower()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest() if norm else None

# active crawl-PROSE rows (the wipe/rebuild scope) on either side of the comparison
_PROSE_WHERE = (
    "is_active=1 AND (created_by IN ('njit_www_crawl','college_crawl','catalog_crawl') "
    "OR (created_by='crawler' AND json_extract(metadata,'$.entity_id') IS NULL))"
)


def _prose_map(conn) -> dict:
    """canonical URL -> (max real-content length, fingerprint of the fullest row) for that URL."""
    out: dict[str, tuple] = {}
    for nk, src, content in conn.execute(
            f"SELECT json_extract(metadata,'$.natural_key'), source_url, content "
            f"FROM knowledge_items WHERE {_PROSE_WHERE}"):
        canon = canonical_prose_url(nk or src or "")
        qlen = prose_quality_len(content)
        if canon and qlen > out.get(canon, (-1, None))[0]:
            out[canon] = (qlen, _fingerprint(content))
    return out


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


def _single_canonical_ok(conn) -> list:
    """canonical URLs with >1 active prose row in the rebuilt DB (should be empty)."""
    rows = conn.execute(
        f"SELECT json_extract(metadata,'$.natural_key') nk, COUNT(*) c "
        f"FROM knowledge_items WHERE {_PROSE_WHERE} GROUP BY nk HAVING c > 1").fetchall()
    return [r[0] for r in rows]


def coverage_gate(rebuilt_conn, backup_conn, *, drop_list=(), drop_pred=None, tolerance=0.05) -> dict:
    """Fail-closed content-aware coverage of rebuilt vs backup. Returns a report dict with `ok`.

    ``drop_list`` — explicit reviewed URLs the rebuild intentionally omits (each canonicalized).
    ``drop_pred`` — optional callable(canonical_url)->bool for a WHOLE CLASS of reviewed drops
    (e.g. math course-syllabus PDFs) so the caller need not enumerate hundreds of URLs. A backup
    URL is excused from the coverage check when it is in drop_list OR drop_pred(url) is True."""
    drop = {canonical_prose_url(u) for u in drop_list}
    rebuilt = _prose_map(rebuilt_conn)
    backup = _prose_map(backup_conn)
    # content present in the rebuilt corpus under ANY URL — so a page NJIT renamed (slug change,
    # /node/N → clean URL, trailing-slash redirect) is recognized as covered, not "missing". The
    # goal is lose no CONTENT; URL drift on a months-newer crawl is expected and not a loss.
    rebuilt_fps = {fp for _, fp in rebuilt.values() if fp}

    missing, thinner, dropped_by_pred, relocated = [], [], [], []
    for url, (blen, bfp) in backup.items():
        if url in drop:
            continue
        if drop_pred is not None and drop_pred(url):
            dropped_by_pred.append(url)          # audited so a reviewed class-drop is actually reviewable
            continue
        if url not in rebuilt:
            if bfp is not None and bfp in rebuilt_fps:
                relocated.append(url)            # same content, different URL — covered, not lost
            else:
                missing.append(url)
        elif rebuilt[url][0] < blen * (1 - tolerance):
            thinner.append(url)

    preserve_before = _preserve_counts(backup_conn)
    preserve_after = _preserve_counts(rebuilt_conn)
    preserve_ok = preserve_before == preserve_after
    dup_canon = _single_canonical_ok(rebuilt_conn)

    ok = not missing and not thinner and preserve_ok and not dup_canon
    return {
        "ok": ok,
        "missing_urls": sorted(missing),
        "thinner_urls": sorted(thinner),
        "preserve_ok": preserve_ok,
        "preserve_before": preserve_before,
        "preserve_after": preserve_after,
        "dup_canonical": dup_canon,
        "dropped_by_pred": sorted(dropped_by_pred),
        "relocated_urls": sorted(relocated),
        "backup_prose_urls": len(backup),
        "rebuilt_prose_urls": len(rebuilt),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Content-aware prose rebuild coverage gate")
    ap.add_argument("--rebuilt", required=True)
    ap.add_argument("--backup", required=True)
    ap.add_argument("--tolerance", type=float, default=0.05)
    args = ap.parse_args(argv)
    from v2.core.database.schema import get_connection
    # Owner 2026-07-01: KEEP everything from NJIT — the corpus drops NO content (syllabi kept+labeled;
    # homepage + orphaned pages recovered as singletons). The only reviewed drops are STALE URL ALIASES
    # whose redirect TARGET is already present in rebuilt (verified live: /node/N and old slugs that
    # 301 to a clean URL we DID crawl) — dropping the dead alias loses nothing.
    ALIAS_DROPS = [
        "https://appliedengineering.njit.edu/construction-engineering-technology",
        "https://ist.njit.edu/get-started-ist-services-employee",
        "https://ist.njit.edu/node/1599",
        "https://www.njit.edu/careerservices/node/176",
        "https://www.njit.edu/careerservices/node/201",
        "https://www.njit.edu/careerservices/node/206",
        "https://www.njit.edu/careerservices/node/231",
        "https://www.njit.edu/careerservices/node/236",
        "https://www.njit.edu/careerservices/node/241",
        "https://www.njit.edu/financialaid/node/36",
        "https://www.njit.edu/registrar",                  # nav landing → /registrar/ (subtree crawled)
        # Site-TRIMMED page: NJIT shortened it; rebuilt (qlen 88) matches the CURRENT live page exactly,
        # the backup (qlen 154) is the older longer version. Rebuilt is faithful → not a loss.
        "https://ist.njit.edu/banner",
    ]
    res = coverage_gate(get_connection(args.rebuilt), get_connection(args.backup),
                        drop_list=ALIAS_DROPS, tolerance=args.tolerance)
    print(f"backup prose URLs: {res['backup_prose_urls']}  rebuilt: {res['rebuilt_prose_urls']}")
    print(f"missing: {len(res['missing_urls'])}  thinner: {len(res['thinner_urls'])}  "
          f"relocated(url-drift, content present): {len(res['relocated_urls'])}  "
          f"dup_canonical: {len(res['dup_canonical'])}  preserve_ok: {res['preserve_ok']}")
    if res["dropped_by_pred"]:
        print(f"reviewed drops (by drop_pred): {len(res['dropped_by_pred'])}")
        for u in res["dropped_by_pred"][:8]:
            print("   drop:", u)
    if res["missing_urls"][:20]:
        print("MISSING (first 20):", res["missing_urls"][:20])
    if res["thinner_urls"][:20]:
        print("THINNER (first 20):", res["thinner_urls"][:20])
    if not res["preserve_ok"]:
        print("PRESERVE MISMATCH:", res["preserve_before"], "->", res["preserve_after"])
    print("\nGATE:", "PASS ✅" if res["ok"] else "FAIL ❌ (do NOT swap)")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
