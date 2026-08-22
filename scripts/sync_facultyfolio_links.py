#!/usr/bin/env python3
"""Sync every active NJIT person's FacultyFolio link with the published folio site.

The bot is responsible for showing a FacultyFolio link for ALL NJIT faculty people
(owner, 2026-08-22) — nothing more (no involvement in the folio repo itself). This is
the re-runnable sync: run it whenever the folio roster changes.

Source of truth: https://facultyfolio.github.io/njit/sitemap.xml — person pages are
/njit/p/<slug>.html and the slug equals the NJIT profile slug (UCID), i.e. the last
segment of our Person node key people.njit.edu/profile/<slug>.

Per person (active only):
  - slug in sitemap, no folio URL stored  -> ADD attrs.profiles.facultyfolio.url
  - slug in sitemap, URL stored but stale -> FIX to the canonical URL
  - folio URL stored, slug NOT in sitemap -> REMOVE the facultyfolio entry (the page
    is gone; a stored link would 404 on answers). Other profiles are untouched.
  - inactive people are never touched; sitemap slugs matching no active person are
    reported (folio may keep departed people we've retired).

EXTRA_KEYS maps folio slugs to manual (dashboard-sourced) nodes that have no
people.njit.edu key. Gated: dry-run by default, --commit takes a hardened_backup.
"""
import argparse
import json
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _area_tag_migrate import hardened_backup  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SITEMAP_URL = "https://facultyfolio.github.io/njit/sitemap.xml"
PAGE_TEMPLATE = "https://facultyfolio.github.io/njit/p/{slug}.html"
KEY_PREFIX = "people.njit.edu/profile/"
# Folio slugs whose person lives under a manual dashboard key (no NJIT profile key).
EXTRA_KEYS = {
    "pelesko": ["dashboard/njit-administration/john-pelesko", "dashboard/njit/john-pelesko"],
}


def fetch_sitemap_slugs(source: str) -> set[str]:
    if source.startswith("http"):
        with urllib.request.urlopen(source, timeout=30) as r:
            xml = r.read().decode("utf-8", "replace")
        locs = re.findall(r"<loc>([^<]+)</loc>", xml)
        return {m.group(1) for loc in locs if (m := re.search(r"/njit/p/([^/]+)\.html$", loc))}
    return {s.strip() for s in Path(source).read_text().splitlines() if s.strip()}


def sync(conn: sqlite3.Connection, slugs: set[str], commit: bool) -> dict:
    stats = {"added": 0, "fixed": 0, "removed": 0, "kept": 0, "unmatched": []}
    slug_to_keys = {s: [KEY_PREFIX + s] for s in slugs}
    for s, keys in EXTRA_KEYS.items():
        if s in slugs:
            slug_to_keys[s] = keys

    rows = conn.execute(
        "SELECT id, key, name, attrs FROM nodes WHERE type='Person' AND is_active=1").fetchall()
    by_key = {key: (nid, name, raw) for nid, key, name, raw in rows}

    def write(nid: int, attrs: dict) -> None:
        if commit:
            conn.execute("UPDATE nodes SET attrs=? WHERE id=?", (json.dumps(attrs), nid))

    matched_ids = set()
    for slug, keys in sorted(slug_to_keys.items()):
        hits = [by_key[k] for k in keys if k in by_key]
        if not hits:
            stats["unmatched"].append(slug)
            continue
        url = PAGE_TEMPLATE.format(slug=slug)
        for nid, name, raw in hits:
            matched_ids.add(nid)
            attrs = json.loads(raw) if raw else {}
            cur = attrs.get("profiles", {}).get("facultyfolio", {}).get("url")
            if cur == url:
                stats["kept"] += 1
                continue
            attrs.setdefault("profiles", {}).setdefault("facultyfolio", {})["url"] = url
            action = "FIX" if cur else "ADD"
            print(f"  {action} {name}: {url}" + (f"  (was {cur})" if cur else ""))
            stats["fixed" if cur else "added"] += 1
            write(nid, attrs)

    for nid, key, name, raw in rows:
        if nid in matched_ids or not raw:
            continue
        attrs = json.loads(raw)
        folio = attrs.get("profiles", {}).get("facultyfolio")
        if folio and folio.get("url"):
            print(f"  REMOVE {name}: {folio['url']} (page no longer published)")
            attrs["profiles"].pop("facultyfolio")
            stats["removed"] += 1
            write(nid, attrs)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--sitemap", default=SITEMAP_URL,
                    help="sitemap URL, or a local file of slugs one per line")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    slugs = fetch_sitemap_slugs(args.sitemap)
    if not slugs:
        sys.exit("Sitemap yielded 0 person slugs — refusing to run (a sync now would only remove links).")
    print(f"{len(slugs)} published person pages in the folio sitemap.")
    if args.commit:
        hardened_backup(args.db, "facultyfolio-sync")
    conn = sqlite3.connect(args.db)
    try:
        stats = sync(conn, slugs, args.commit)
        if args.commit:
            conn.commit()
    finally:
        conn.close()
    mode = "COMMITTED" if args.commit else "DRY-RUN (no writes; re-run with --commit)"
    print(f"\n{mode}: {stats['added']} added, {stats['fixed']} fixed, "
          f"{stats['removed']} removed, {stats['kept']} already correct.")
    if stats["unmatched"]:
        print(f"  {len(stats['unmatched'])} published slug(s) match no ACTIVE person "
              f"(departed or unknown): {', '.join(stats['unmatched'])}")


if __name__ == "__main__":
    main()
