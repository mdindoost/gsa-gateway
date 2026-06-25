#!/usr/bin/env python3
"""WorldCup shadow comparison — ESPN vs football-data, READ-ONLY, posts NOTHING.

Answers "is ESPN faster than the API we use?" with hard numbers. Each tick it fetches
BOTH sources for today's matches, joins them by kickoff, and records the wall-clock time
each source FIRST reports a given scoreline. When both have reported it, it prints the
delta (positive ⇒ ESPN led). Nothing is enqueued or posted; the live engine is untouched.

Usage:
  python scripts/wc_shadow_compare.py [--interval 5] [--day 2026-06-24] [--minutes 90]
                                      [--log logs/wc_shadow.log]

Stop with Ctrl-C; it prints a summary of average lead time.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import os
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
load_dotenv()

from v2.integration.wc_providers.espn import EspnProvider, BlockedError
from v2.integration.wc_providers.shadow import compare, kickoff_key
from v2.integration.worldcup_tracker import BASE_URL


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _stamp() -> str:
    return _now().strftime("%H:%M:%S")


async def _fd_fetch(key: str, day: str) -> list[dict]:
    """football-data matches for the ET day window (mirrors the live engine's query)."""
    nxt = (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()
    url = f"{BASE_URL}/competitions/WC/matches?dateFrom={day}&dateTo={nxt}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers={"X-Auth-Token": key},
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    return (await r.json()).get("matches", [])
                print(f"[{_stamp()}] football-data HTTP {r.status}")
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        print(f"[{_stamp()}] football-data error: {exc}")
    return []


async def run(interval: int, day: str, minutes: int, log_path: Path) -> None:
    fd_key = (os.getenv("FOOTBALL_API_KEY") or "").split(",")[0].strip()
    if not fd_key:
        print("No FOOTBALL_API_KEY in .env — cannot compare.")
        return
    espn = EspnProvider()
    # first_seen[(kickoff, "H-A")] = {"espn": ts, "fd": ts}
    first_seen: dict[tuple, dict] = {}
    leads: list[float] = []
    log = log_path.open("a", encoding="utf-8")
    log.write(f"\n=== shadow run {_now().isoformat()} day={day} interval={interval}s ===\n")
    deadline = _now() + datetime.timedelta(minutes=minutes)
    print(f"Shadow comparing ESPN vs football-data for {day} every {interval}s "
          f"(until {deadline.strftime('%H:%M')}Z). Ctrl-C to stop.\n")

    try:
        while _now() < deadline:
            try:
                espn_matches = await espn.fetch_matches(et_day=day)
            except BlockedError:
                print(f"[{_stamp()}] ESPN blocked — backing off")
                espn_matches = []
            fd_matches = await _fd_fetch(fd_key, day)
            rows = compare(espn_matches, fd_matches)
            now = _now()
            for r in rows:
                if not r.get("matched"):
                    continue
                # record first-seen wall-clock per source for each scoreline
                for src, score in (("espn", r["espn_score"]), ("fd", r["fd_score"])):
                    line = f"{score[0]}-{score[1]}"
                    slot = first_seen.setdefault((r["kickoff"], line), {})
                    if src not in slot:
                        slot[src] = now
                        # when BOTH have now reported this line, compute the lead
                        if "espn" in slot and "fd" in slot:
                            delta = (slot["fd"] - slot["espn"]).total_seconds()
                            leads.append(delta)
                            who = "ESPN" if delta > 0 else "football-data"
                            msg = (f"[{_stamp()}] {r['teams']} {line}: "
                                   f"{who} led by {abs(delta):.0f}s")
                            print("  " + msg)
                            log.write(msg + "\n")
                if not r["scores_agree"]:
                    line = (f"[{_stamp()}] DISAGREE {r['teams']}: "
                            f"ESPN {r['espn_score']} vs fd {r['fd_score']}")
                    print(line); log.write(line + "\n")
            log.flush()
            await asyncio.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        _summary(leads, log)
        log.close()


def _summary(leads: list[float], log) -> None:
    if not leads:
        msg = "No scoreline was seen by both sources during the run (no goals, or one source idle)."
    else:
        espn_ahead = [d for d in leads if d > 0]
        avg = sum(leads) / len(leads)
        msg = (f"SUMMARY: {len(leads)} scorelines compared. "
               f"ESPN led on {len(espn_ahead)}/{len(leads)}. "
               f"Avg ESPN lead {avg:+.1f}s (positive ⇒ ESPN faster).")
    print("\n" + msg)
    log.write(msg + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=5)
    ap.add_argument("--day", default=datetime.date.today().isoformat())
    ap.add_argument("--minutes", type=int, default=120)
    ap.add_argument("--log", default="logs/wc_shadow.log")
    a = ap.parse_args()
    log_path = REPO_ROOT / a.log
    log_path.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(run(a.interval, a.day, a.minutes, log_path))


if __name__ == "__main__":
    main()
