#!/usr/bin/env python3
"""Slow-drip Scholar URL discovery across ALL faculty-without-Scholar (long-running, resumable).

Gated: dry-run by default (counts who'd be searched + ETA, no fetch/write). --commit takes ONE
hardened backup, then drips ~50/hr (per-fetch jitter), auto-writing only strict verified-njit
matches, queuing uncertain to a review CSV, marking every non-strict outcome so it never re-searches
(terminates + resumes), backing off if Scholar throttles, and embedding new areas once at the end.
Runs detached; SIGTERM/SIGINT stops cleanly after the current person. Spec:
docs/superpowers/specs/2026-06-20-scholar-discovery-sweep-design.md

  python scripts/discover_scholar_sweep.py --org nce                       # dry-run: count + ETA
  nohup python scripts/discover_scholar_sweep.py --budget 500 --commit &   # all NJIT, capped at 500 searches
"""
from __future__ import annotations

import argparse
import csv
import datetime
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.ingestion import scholar_discovery as D
from v2.integration.njit_search import web_search
from v2.core.ingestion.scholar import default_fetch

_stop = {"flag": False}


def _install_signal_handlers():
    def _handle(signum, frame):
        _stop["flag"] = True
        print(f"\n[signal {signum}] stopping after the current person…", flush=True)
    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


def _interruptible_sleep(seconds: float) -> None:
    end = time.time() + seconds
    while not _stop["flag"]:
        remaining = end - time.time()
        if remaining <= 0:
            break
        time.sleep(min(1.0, remaining))


def _fmt_eta(n: int, avg_s: float) -> str:
    hrs = n * avg_s / 3600.0
    return f"~{hrs:.1f} h (no-block best case; real runs slower due to backoff)"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--org", "--department", dest="org", help="scope to an org slug (subtree); default = all NJIT")
    ap.add_argument("--chunk", type=int, default=50)
    ap.add_argument("--budget", type=int, default=None, help="MAX Brave searches this run (REQUIRED for --commit; the month's headroom — pool is shared with the live fallback)")
    ap.add_argument("--jitter-min", type=int, default=45)
    ap.add_argument("--jitter-max", type=int, default=100)
    ap.add_argument("--backoff-hours", type=float, default=3.0)
    ap.add_argument("--retry-after-days", type=int, default=None, help="re-attempt people last tried > N days ago")
    ap.add_argument("--commit", action="store_true", help="actually search + write (else dry-run)")
    args = ap.parse_args(argv)

    conn = get_connection(args.db)
    remaining = D.select_discovery_targets(conn, org_scope=args.org,
                                           retry_after_days=args.retry_after_days)
    avg = (args.jitter_min + args.jitter_max) / 2.0
    print(f"{len(remaining)} faculty without a Scholar URL (or prior attempt) in scope "
          f"'{args.org or 'all NJIT'}'. ETA at the drip rate: {_fmt_eta(len(remaining), avg)}")
    if not os.getenv("BRAVE_API_KEY"):
        print("⚠️  BRAVE_API_KEY is not set — discovery searches will return nothing.")

    if not args.commit:
        print("\nDRY-RUN. Re-run with --commit --budget N to start the sweep.")
        return 0
    if not os.getenv("BRAVE_API_KEY"):
        ap.error("BRAVE_API_KEY required for --commit")
    if args.budget is None:
        ap.error("--budget is required for --commit (set it to this month's remaining Brave headroom)")
    if not remaining:
        print("Nothing to do."); return 0

    # Defensive WAL + a long busy_timeout so the live bot's analytics writes interleave cleanly.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    _install_signal_handlers()
    print(f"\nBackup: {hardened_backup(args.db, 'pre-scholar-sweep')}")

    csv_path = REPO / "logs" / f"scholar_review_sweep_{datetime.date.today():%Y%m%d}.csv"
    csv_path.parent.mkdir(exist_ok=True)
    new_file = not csv_path.exists()
    csv_f = open(csv_path, "a", newline="")
    csv_w = csv.writer(csv_f)
    if new_file:
        csv_w.writerow(["person_key", "name", "candidate_url", "reason"])

    def on_progress(stats, key, name, decision):
        if decision == "uncertain":
            q = stats["queue"][-1]
            csv_w.writerow([q[0], q[1], q[2], q[3]]); csv_f.flush()
        if stats["scanned"] % 10 == 0:
            print(f"  [{datetime.datetime.now():%H:%M:%S}] scanned={stats['scanned']} "
                  f"written={stats['written']} queued={stats['queued']} blocked={stats['blocked']} "
                  f"brave={stats['brave_calls']}", flush=True)

    stats = D.sweep(
        conn, web_search=web_search, fetch=default_fetch, sleep=_interruptible_sleep,
        org_scope=args.org, chunk=args.chunk, brave_budget=args.budget,
        jitter=(args.jitter_min, args.jitter_max), backoff_seconds=int(args.backoff_hours * 3600),
        retry_after_days=args.retry_after_days, should_stop=lambda: _stop["flag"], on_progress=on_progress)
    csv_f.close()
    print(f"\nScholar discovery sweep complete ({stats['stopped_reason']}): "
          f"{stats['written']} written, {stats['queued']} queued, {stats['skipped']} skipped, "
          f"{stats['blocked']} blocked of {stats['scanned']} scanned ({stats['brave_calls']} Brave calls).")
    print(f"Review queue appended to: {csv_path}")

    if stats["written"]:
        print("\nEmbedding new research-area items…")
        try:
            subprocess.run([sys.executable, str(REPO / "v2" / "scripts" / "embed_all.py"), args.db],
                           cwd=str(REPO))
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ embed failed ({exc}) — data committed; run embed_all when Ollama is up.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
