#!/usr/bin/env python3
"""Gated batch data-correction: recover <br>-collapsed research areas, university-wide.

THE BUG (producer): NJIT profiles list Research Interests as <br>-separated lines, but
the crawler's _clean() flattens <br> to a space and _split_areas() only splits on ','/';'.
So a person whose interests use NO commas (only <br>) yields ZERO clean areas — the list
survives only as a collapsed `research_statement` blob (Type A), or, when a first comma'd
line glued the rest, as ONE run-on ResearchArea node (Type B, e.g. Guiling Wang).

THE FIX (data-only, no crawler change — owner: "do it on the DB, no worry about crawling"):
re-fetch each affected person's live NJIT page, convert <br> -> '; ' BEFORE the EXISTING
parser runs (so `_split_top_level` splits correctly), and reconcile their structured
`researches` edges (source='crawler', area_source='structured') to the recovered list —
exactly the artifacts a correctly-crawled faculty has. Mirrors scripts/_gwang_research_area_fix.py
at scale. QUICK PATCH — the parser bug is left; a future re-crawl re-collapses (owner accepted).

SAFETY:
  * dry-run by default; --commit writes, with a hardened backup on the live DB.
  * only writes a person when the corrected parse yields >=2 clean areas (the parser's own
    precision rule); 0/1-token results are FLAGGED for review, never written (honest-empty
    preserved — never fabricate an area).
  * source-scoped reconcile: only source='crawler' researches edges are touched; scholar/
    external edges are never modified.
  * politeness delay between fetches; project User-Agent (njit_adapter.UA).
Run:  python scripts/_br_collapse_batch_fix.py [--db X] [--limit N] [--commit] [--report FILE]
Re-embed is NOT needed (edges only; no knowledge_items written).
"""
import argparse, csv, os, re, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import sqlite3
from bs4 import BeautifulSoup
from scripts._area_tag_migrate import hardened_backup
from v2.core.ingestion.njit_adapter import fetch, _is_area_token, _AREA_STOPWORDS
from v2.core.graph.store import (upsert_node, upsert_edge,
                                 active_edge_ids_from, deactivate_edges)
from v2.core.graph.project import area_key

LIVE_DB = os.path.join(ROOT, "gsa_gateway.db")
_BR = re.compile(r"(?i)<br\s*/?>")
_SUSPECT_WORDS = 6          # a crawler area with > this many words is a likely run-on collapse
_MAX_CLEAN_AREAS = 8        # more than this from one desc => not a clean interest list (lab page)
_SKIP = {"kroz"}            # owner-reviewed: NJIT "interests" field holds AWARDS, not research areas


def affected_people(conn):
    """Union of Type A (research_statement + 0 area edges) and Type B (linked to a run-on
    crawler ResearchArea). Over-inclusion is safe: an unchanged person just re-derives to
    the same areas and is reported UNCHANGED (no write)."""
    a = conn.execute("""
        SELECT n.id, n.key, n.name FROM nodes n
        WHERE n.type='Person' AND n.is_active=1
          AND EXISTS(SELECT 1 FROM knowledge_items k WHERE k.is_active=1
                     AND k.type='research_statement'
                     AND json_extract(k.metadata,'$.entity_id')=n.key)
          AND NOT EXISTS(SELECT 1 FROM edges e WHERE e.src_id=n.id
                         AND e.type='researches' AND e.is_active=1)
    """).fetchall()
    b = conn.execute(f"""
        SELECT DISTINCT n.id, n.key, n.name FROM nodes n
        JOIN edges e ON e.src_id=n.id AND e.type='researches' AND e.is_active=1
        JOIN nodes ra ON ra.id=e.dst_id
        WHERE n.type='Person' AND n.is_active=1 AND e.source='crawler'
          AND (LENGTH(ra.name)-LENGTH(REPLACE(ra.name,' ','')))+1 > {_SUSPECT_WORDS}
    """).fetchall()
    seen, out = set(), []
    for pid, key, name in list(a) + list(b):
        if pid not in seen and key.rsplit("/", 1)[-1] not in _SKIP:
            seen.add(pid)
            out.append((pid, key, name))
    return out


def current_areas(conn, pid):
    return [r[0] for r in conn.execute(
        "SELECT ra.name FROM edges e JOIN nodes ra ON ra.id=e.dst_id "
        "WHERE e.src_id=? AND e.type='researches' AND e.is_active=1 AND e.source='crawler' "
        "ORDER BY e.id", (pid,))]


_HEADER_ECHO = re.compile(r"(?i)^(current\s+|selected\s+)?research\s+(interests?|areas?)\b")


def _clean_line(html_line: str) -> str:
    """One <br>-delimited fragment -> a clean area string (or '')."""
    t = BeautifulSoup(html_line, "html.parser").get_text(" ", strip=True)
    t = t.replace("​", "").replace(" ", " ")          # zero-width / nbsp
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^[\(\[]?\s*\d+\s*[\).\]]\s*", "", t)           # leading enumeration: 1) (1) 2.
    t = re.sub(r"(?i)^(?:and|&|[-*•·▪◦●○–—])\s*", "", t).strip(" .,;:-–—*•·▪◦●○").strip()
    if _HEADER_ECHO.match(t):                                    # a "Research interests" label echo
        return ""
    return t


def recover_areas(key):
    """Fetch the live page and recover the Research Interests as a clean area list, splitting
    ONLY on <br> (one area per line — faithful to NJIT's visual list; internal commas within a
    line are NOT area boundaries, so 'Applied AI in Finance, Transportation, ...' stays whole).
    Returns (areas, suspect): suspect=True when the block looks like a lab page / prose (a URL,
    a 'Label:' header, or >_MAX_CLEAN_AREAS lines) — those are flagged, never auto-written.
    """
    html = fetch("https://" + key)
    soup = BeautifulSoup(html, "html.parser")
    desc = None
    for label in soup.find_all("div", class_="label"):
        if label.get_text(strip=True).lower().startswith("research interest"):
            desc = label.find_next_sibling("div")
            break
    if desc is None:
        return [], False
    inner = desc.decode_contents()
    suspect = bool(re.search(r"https?://", inner) or desc.find("a"))
    areas, seen = [], set()
    for frag in _BR.split(inner):
        t = _clean_line(frag)
        if not t or t.lower() in _AREA_STOPWORDS or not re.search(r"[A-Za-z]", t):
            continue
        if t.endswith(":") or re.search(r"https?://", t):     # section header / URL line
            suspect = True
            continue
        if not _is_area_token(t):                             # prose (colon, '. word', >12 words)
            continue
        k = t.lower()
        if k not in seen:
            seen.add(k)
            areas.append(t)
    if len(areas) > _MAX_CLEAN_AREAS:                         # too many -> not a clean list
        suspect = True
    return areas, suspect


def reconcile(conn, pid, areas):
    """Write the recovered areas as source='crawler'/area_source='structured' edges and
    deactivate this person's stale crawler researches edges (Guiling pattern)."""
    keep = set()
    for a in areas:
        anode = upsert_node(conn, type="ResearchArea", key=area_key(a), name=a, source="crawler")
        keep.add(upsert_edge(conn, src_id=pid, type="researches", dst_id=anode,
                             area_source="structured", source="crawler"))
    sweep = active_edge_ids_from(conn, pid, type="researches", source="crawler") - keep
    deactivate_edges(conn, sweep)
    return keep, sweep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=LIVE_DB)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="process only first N (testing)")
    ap.add_argument("--only", default="", help="comma-separated slugs to process (targeted retry)")
    ap.add_argument("--delay", type=float, default=0.6, help="politeness delay between fetches")
    ap.add_argument("--report", default=os.path.join(ROOT, "scratchpad", "br_collapse_report.csv"))
    args = ap.parse_args()

    if args.commit and os.path.abspath(args.db) == os.path.abspath(LIVE_DB):
        hardened_backup(args.db, "br-collapse-batch-fix")

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    people = affected_people(conn)
    if args.only:
        want = {s.strip() for s in args.only.split(",") if s.strip()}
        people = [p for p in people if p[1].rsplit("/", 1)[-1] in want]
    if args.limit:
        people = people[:args.limit]
    print(f"Affected people to process: {len(people)}  (commit={args.commit})\n")

    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    rep = open(args.report, "w", newline="")
    w = csv.writer(rep)
    w.writerow(["slug", "name", "status", "n_before", "n_after", "before", "after"])

    tally = {"FIXED": 0, "UNCHANGED": 0, "FLAGGED_review": 0, "FLAGGED_empty": 0,
             "FLAGGED_single": 0, "FETCH_ERROR": 0}
    for i, (pid, key, name) in enumerate(people, 1):
        slug = key.rsplit("/", 1)[-1]
        before = current_areas(conn, pid)
        try:
            areas, suspect = recover_areas(key)
        except Exception as e:                       # noqa: BLE001 — network is best-effort
            tally["FETCH_ERROR"] += 1
            w.writerow([slug, name, "FETCH_ERROR", len(before), "", " | ".join(before), str(e)[:120]])
            print(f"[{i}/{len(people)}] {slug:12} FETCH_ERROR {e}")
            time.sleep(args.delay); continue

        if suspect:                                  # lab page / prose / URL — needs a human, never auto-write
            tally["FLAGGED_review"] += 1
            w.writerow([slug, name, "FLAGGED_review", len(before), len(areas),
                        " | ".join(before), " | ".join(areas)])
            print(f"[{i}/{len(people)}] {slug:12} FLAGGED_review ({len(areas)} candidates)")
            time.sleep(args.delay); continue

        if len(areas) < 2:
            status = "FLAGGED_empty" if not areas else "FLAGGED_single"
            tally[status] += 1
            w.writerow([slug, name, status, len(before), len(areas),
                        " | ".join(before), " | ".join(areas)])
            print(f"[{i}/{len(people)}] {slug:12} {status}: {areas or '—'}")
            time.sleep(args.delay); continue

        if [a.lower() for a in before] == [a.lower() for a in areas]:
            tally["UNCHANGED"] += 1
            w.writerow([slug, name, "UNCHANGED", len(before), len(areas),
                        " | ".join(before), " | ".join(areas)])
            time.sleep(args.delay); continue

        reconcile(conn, pid, areas)
        tally["FIXED"] += 1
        w.writerow([slug, name, "FIXED", len(before), len(areas),
                    " | ".join(before), " | ".join(areas)])
        print(f"[{i}/{len(people)}] {slug:12} FIXED {len(before)}->{len(areas)}: {areas}")
        time.sleep(args.delay)

    rep.close()
    print("\n=== TALLY ===")
    for k, v in tally.items():
        print(f"  {k:16} {v}")
    print("report ->", args.report)

    if args.commit:
        conn.commit()
        print("\n✅ COMMITTED to", args.db)
    else:
        conn.rollback()
        print("\n(dry-run — nothing written. Re-run with --commit to apply.)")
    conn.close()


if __name__ == "__main__":
    main()
