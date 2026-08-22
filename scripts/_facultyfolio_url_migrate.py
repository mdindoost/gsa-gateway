#!/usr/bin/env python3
"""Remap stored FacultyFolio page URLs to the site's 2026-08-02 multi-university layout.

FacultyFolio (facultyfolio.github.io) moved every NJIT person page from
/p/<slug>.html to /njit/p/<slug>.html with NO redirects (confirmed with the
Faculty-Pages deploy: the build prunes the old root-level pages). This rewrites
each Person node's attrs.profiles.facultyfolio.url accordingly. Slugs are
unchanged, so only the path prefix moves.

Safety: a slug must appear in the sitemap slug list (--slugs file, one slug per
line, taken from https://facultyfolio.github.io/njit/sitemap.xml) or the row is
left untouched and reported — a missing slug means the folio roster dropped the
person and is a question for the owner, not a URL to rewrite blindly.

Gated: dry-run by default, --commit takes a hardened_backup first.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _area_tag_migrate import hardened_backup  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
OLD_PREFIX = "https://facultyfolio.github.io/p/"
NEW_PREFIX = "https://facultyfolio.github.io/njit/p/"


def migrate(conn: sqlite3.Connection, sitemap_slugs: set[str], commit: bool) -> tuple[int, list[str]]:
    rewritten, skipped = 0, []
    rows = conn.execute(
        "SELECT id, name, attrs FROM nodes WHERE type='Person' "
        "AND json_extract(attrs,'$.profiles.facultyfolio.url') IS NOT NULL").fetchall()
    for nid, name, raw in rows:
        attrs = json.loads(raw)
        url = attrs["profiles"]["facultyfolio"]["url"]
        if url.startswith(NEW_PREFIX):
            continue                              # already migrated (idempotent re-run)
        if not (url.startswith(OLD_PREFIX) and url.endswith(".html")):
            skipped.append(f"{name}: unexpected URL {url}")
            continue
        slug = url[len(OLD_PREFIX):-len(".html")]
        if slug not in sitemap_slugs:
            skipped.append(f"{name}: slug '{slug}' not in the njit sitemap — left untouched")
            continue
        attrs["profiles"]["facultyfolio"]["url"] = NEW_PREFIX + slug + ".html"
        print(f"  {name}: /p/{slug}.html -> /njit/p/{slug}.html")
        if commit:
            conn.execute("UPDATE nodes SET attrs=? WHERE id=?", (json.dumps(attrs), nid))
        rewritten += 1
    return rewritten, skipped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--slugs", required=True, help="file of published slugs, one per line (from the njit sitemap)")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    sitemap_slugs = {s.strip() for s in Path(args.slugs).read_text().splitlines() if s.strip()}
    if args.commit:
        hardened_backup(args.db, "facultyfolio-url")
    conn = sqlite3.connect(args.db)
    try:
        rewritten, skipped = migrate(conn, sitemap_slugs, args.commit)
        if args.commit:
            conn.commit()
    finally:
        conn.close()
    mode = "COMMITTED" if args.commit else "DRY-RUN (no writes; re-run with --commit)"
    print(f"\n{mode}: {rewritten} URL(s) rewritten, {len(skipped)} skipped.")
    for s in skipped:
        print(f"  SKIPPED {s}")


if __name__ == "__main__":
    main()
