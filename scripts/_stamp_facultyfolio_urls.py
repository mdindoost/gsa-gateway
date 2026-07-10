#!/usr/bin/env python3
"""Stamp each published person's FacultyFolio profile URL into the KG.

Writes `attrs.profiles.facultyfolio = {"url": "<SITE_ORIGIN>/p/<slug>.html"}` on every Person node
that FacultyFolio actually publishes, so the bot can later surface ONE FacultyFolio link in place of
the scattered external links. (Bot RENDERING is a separate, owner-owned change — this script only
writes the DATA field; an unregistered profiles key is simply ignored by the bot until wired.)

**Source of truth = the built profile files** (`config.OUT_ROOT/p/*.html`): a URL is stamped iff its
page actually exists, so every stamped link resolves. Re-runnable + idempotent: it also CLEARS the
field from any node that has it but is no longer published (e.g. after an unpublish/rebuild). Run it
after each FacultyFolio build; later it can be wired as a post-build step (automation roadmap).

Dry-run by default; `--commit` takes a hardened_backup first.
"""
import argparse
import glob
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts._area_tag_migrate import hardened_backup  # noqa: E402
from facultyfolio import config  # noqa: E402

KEY_TMPL = "people.njit.edu/profile/{}"


def published_urls():
    """slug -> absolute FacultyFolio URL, from the actually-built profile pages."""
    out = {}
    for f in glob.glob(os.path.join(config.OUT_ROOT, "p", "*.html")):
        slug = os.path.basename(f)[:-5]
        out[slug] = f"{config.SITE_ORIGIN}/p/{slug}.html"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="gsa_gateway.db")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    urls = published_urls()
    published_keys = {KEY_TMPL.format(s): u for s, u in urls.items()}
    print(f"Published FacultyFolio profiles found: {len(urls)}")

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    people = conn.execute(
        "SELECT id, key, attrs FROM nodes WHERE type='Person' AND is_active=1"
    ).fetchall()

    to_set, to_clear = [], []          # (id, new_attrs_json)
    for p in people:
        attrs = json.loads(p["attrs"]) if p["attrs"] else {}
        profiles = attrs.get("profiles") or {}
        cur = (profiles.get("facultyfolio") or {}).get("url")
        want = published_keys.get(p["key"])
        if want:
            if cur != want:
                profiles["facultyfolio"] = {**(profiles.get("facultyfolio") or {}), "url": want}
                attrs["profiles"] = profiles
                to_set.append((p["id"], json.dumps(attrs), p["key"], want))
        elif cur is not None:          # has the field but is no longer published -> clear
            profiles.pop("facultyfolio", None)
            attrs["profiles"] = profiles
            to_clear.append((p["id"], json.dumps(attrs), p["key"]))

    print(f"  to stamp/update: {len(to_set)}   to clear (unpublished): {len(to_clear)}")
    for _, _, key, url in to_set[:5]:
        print(f"    + {key} -> {url}")
    if len(to_set) > 5:
        print(f"    … +{len(to_set) - 5} more")
    for _, _, key in to_clear:
        print(f"    - clear {key}")

    if not args.commit:
        print("\nDRY RUN — re-run with --commit to write.")
        conn.close()
        return

    if not to_set and not to_clear:
        print("Nothing to change.")
        conn.close()
        return

    hardened_backup(args.db, "folio_urls")
    print("hardened_backup taken.")
    for pid, aj, *_ in to_set:
        conn.execute("UPDATE nodes SET attrs=? WHERE id=?", (aj, pid))
    for pid, aj, *_ in to_clear:
        conn.execute("UPDATE nodes SET attrs=? WHERE id=?", (aj, pid))
    conn.commit()
    conn.close()
    print(f"COMMITTED: {len(to_set)} stamped, {len(to_clear)} cleared.")


if __name__ == "__main__":
    main()
