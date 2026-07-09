#!/usr/bin/env python3
"""Refresh Google Scholar METRICS for exactly the people listed (with a Scholar URL) in a
markdown doc — e.g. docs/faculty_without_scholar_2026-07-09.md after URLs were hand-added.

Why this exists: refresh_scholar.py scopes only by --key (one person) or --org (a subtree).
This runner reads a doc's `- Name:<scholar-url>` lines, resolves each Name to its Person node
(disambiguating a duplicated name by the `### Dept (n)` header it sits under), and feeds that
EXACT key set into scholar.refresh_scholar(only_keys=...). It never touches anyone else.

Gated: dry-run by default (lists the resolved targets); --commit takes a hardened backup, then
fetches + writes metrics. Anti-block flags mirror refresh_scholar.py.

  python scripts/refresh_scholar_from_doc.py                                   # dry-run
  python scripts/refresh_scholar_from_doc.py --commit \
      --jitter-min 20 --jitter-max 60 --fetch-gap 4 --block-abort 5 --embed
  python scripts/refresh_scholar_from_doc.py --doc docs/other.md --commit
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup  # noqa: E402
from v2.core.database.schema import get_connection  # noqa: E402
from v2.core.ingestion import scholar  # noqa: E402

DEFAULT_DOC = REPO / "docs" / "faculty_without_scholar_2026-07-09.md"


def parse_doc(doc_path: Path):
    """Yield (dept_header, name) for every `- Name:<url>` line that carries a Scholar URL."""
    dept = None
    out = []
    for ln in doc_path.read_text().splitlines():
        if ln.startswith("### "):
            dept = ln[4:].strip()
        elif ln.startswith("- "):
            body = ln[2:].strip()
            m = re.search(r"(https?://\S+)", body)
            if not m:
                continue
            name = body[:m.start()].rstrip(": ").strip()
            out.append((dept, name))
    return out


def resolve_key(conn, name, dept):
    """Unique person_key for (name, dept). A name matching >1 active Person node is
    disambiguated by the org whose name is the dept header minus its ` (n)` count suffix."""
    cands = conn.execute(
        "SELECT id, key FROM nodes WHERE type='Person' AND name=? AND is_active=1", (name,)
    ).fetchall()
    if not cands:
        return None, "unmatched"
    if len(cands) == 1:
        return cands[0][1], "unique"
    dept_name = re.sub(r"\s*\(\d+\)\s*$", "", dept or "").strip()
    hits = [
        key for pid, key in cands
        if dept_name in [
            r[0] for r in conn.execute(
                "SELECT n.name FROM edges e JOIN nodes n ON n.id=e.dst_id "
                "WHERE e.type='has_role' AND e.src_id=? AND e.is_active=1", (pid,))
        ]
    ]
    if len(hits) == 1:
        return hits[0], "disambig-by-dept"
    return None, f"ambiguous({len(cands)}) dept={dept_name!r} hits={len(hits)}"


def _run_embed(db_path: str) -> bool:
    cmd = [sys.executable, str(REPO / "v2" / "scripts" / "embed_all.py"), str(db_path)]
    try:
        return subprocess.run(cmd, cwd=str(REPO)).returncode == 0
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ embed failed ({exc}) — metrics are committed; run embed_all when Ollama is up.")
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--doc", default=str(DEFAULT_DOC), help="markdown doc with `- Name:<url>` lines")
    ap.add_argument("--delay", type=float, default=3.0, help="fixed seconds between people (when no jitter)")
    ap.add_argument("--jitter-min", type=int, default=None, help="anti-block: min sec between people (with --jitter-max)")
    ap.add_argument("--jitter-max", type=int, default=None, help="anti-block: max sec between people")
    ap.add_argument("--fetch-gap", type=float, default=0.0, help="anti-block: seconds between a person's 2 fetches")
    ap.add_argument("--block-abort", type=int, default=0, help="anti-block: stop after N consecutive blocked people (0=never)")
    ap.add_argument("--embed", action="store_true", help="embed new research-area items after commit")
    ap.add_argument("--commit", action="store_true", help="actually fetch + write (else dry-run)")
    args = ap.parse_args(argv)
    if (args.jitter_min is None) != (args.jitter_max is None):
        ap.error("--jitter-min and --jitter-max must be given together.")
    jitter = (args.jitter_min, args.jitter_max) if args.jitter_min is not None else None

    doc_path = Path(args.doc)
    if not doc_path.is_absolute():
        doc_path = REPO / doc_path
    conn = get_connection(args.db)

    entries = parse_doc(doc_path)
    keys, problems = [], []
    for dept, name in entries:
        key, how = resolve_key(conn, name, dept)
        (problems if key is None else keys).append((name, dept, how) if key is None else key)

    # de-dupe keys, preserve order
    seen, ordered = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k); ordered.append(k)

    print(f"doc: {doc_path.name} | URL lines {len(entries)} | resolved {len(ordered)} unique keys "
          f"| problems {len(problems)}")
    for name, dept, how in problems:
        print(f"  PROBLEM: {name} [{dept}] -> {how}")
    if problems:
        print("Aborting: resolve every URL row before refreshing."); return 1

    if not args.commit:
        print("\nDRY-RUN. Targets (first 50):")
        for k in ordered[:50]:
            print(f"  {k}")
        print(f"\n{len(ordered)} people would be refreshed. Re-run with --commit.")
        return 0
    if not ordered:
        print("Nothing to refresh."); return 0

    print(f"\nBackup: {hardened_backup(args.db, 'pre-scholar-doc-refresh')}")
    out = scholar.refresh_scholar(conn, only_keys=set(ordered), delay=args.delay, jitter=jitter,
                                  fetch_gap=args.fetch_gap, block_abort=args.block_abort)
    conn.commit()
    if out.get("aborted"):
        print("\n⚠️ ABORTED early after consecutive Scholar blocks — partial run committed; "
              "re-run later to pick up the rest (this runner is idempotent).")
    print(f"\nScholar refresh complete: {out['updated']} updated, {out['areas_updated']} areas, "
          f"{out['failed']} failed of {out['people']}.")
    for key, why in out["errors"][:30]:
        print(f"  ✗ {key}: {why}")
    if out["failed"]:
        print("\n(Failures are expected from raw Scholar scraping — re-run to retry the blocked "
              "ones, or swap a sanctioned provider into scholar.default_fetch.)")
    if args.embed:
        print("\nEmbedding new research-area items…")
        _run_embed(args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
