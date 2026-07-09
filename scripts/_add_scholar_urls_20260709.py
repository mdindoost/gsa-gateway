#!/usr/bin/env python3
"""One-off gated write: add the 145 owner-verified Google Scholar URLs from
docs/faculty_without_scholar_2026-07-09.md into each Person node's attrs.profiles.scholar.url.

Owner verified name + affiliation for every URL (2026-07-09). URL-only (no metrics) —
metrics are a later manual/WebFetch pass. Deep-merges (won't clobber existing scholar dicts).

Dry-run by default. --commit writes live (hardened_backup first). --db overrides target.
"""
import argparse, json, re, sqlite3, sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from v2.core.ingestion.people_editor import set_person_profiles  # noqa: E402
from scripts._area_tag_migrate import hardened_backup  # noqa: E402

DOC = REPO / "docs" / "faculty_without_scholar_2026-07-09.md"


def parse_doc():
    col = dept = None
    rows = []
    for ln in DOC.read_text().splitlines():
        if ln.startswith("## "):
            col = ln[3:].strip()
        elif ln.startswith("### "):
            dept = ln[4:].strip()
        elif ln.startswith("- "):
            body = ln[2:].strip()
            m = re.search(r"(https?://\S+)", body)
            if not m:
                continue
            url = m.group(1)
            name = body[:m.start()].rstrip(": ").strip()
            rows.append((col, dept, name, url))
    return rows


def resolve(conn, name, dept):
    """Return the unique person_key for (name, dept). For a name that maps to >1 Person
    node, disambiguate by the org whose name is the leading token of the file's dept
    header (e.g. 'History (16)' -> 'History')."""
    cands = conn.execute(
        "SELECT id, key FROM nodes WHERE type='Person' AND name=? AND is_active=1", (name,)
    ).fetchall()
    if not cands:
        return None, "unmatched"
    if len(cands) == 1:
        return cands[0][1], "unique"
    dept_name = re.sub(r"\s*\(\d+\)\s*$", "", dept).strip()
    hits = []
    for pid, key in cands:
        orgs = [
            r[0] for r in conn.execute(
                "SELECT n.name FROM edges e JOIN nodes n ON n.id=e.dst_id "
                "WHERE e.type='has_role' AND e.src_id=? AND e.is_active=1", (pid,))
        ]
        if dept_name in orgs:
            hits.append(key)
    if len(hits) == 1:
        return hits[0], "disambig-by-dept"
    return None, f"ambiguous({len(cands)}) dept={dept_name!r} hits={len(hits)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    rows = parse_doc()
    conn = sqlite3.connect(args.db)

    plan, problems = [], []
    for col, dept, name, url in rows:
        key, how = resolve(conn, name, dept)
        if key is None:
            problems.append((name, dept, how))
        else:
            plan.append((key, name, url, how))

    print(f"parsed {len(rows)} URL rows | resolved {len(plan)} | problems {len(problems)}")
    for name, dept, how in problems:
        print(f"  PROBLEM: {name} [{dept}] -> {how}")
    if problems:
        print("Aborting: resolve every row before writing."); return 1

    if not args.commit:
        print("\nDRY-RUN (no write). Sample of resolved plan:")
        for key, name, url, how in plan[:8]:
            print(f"  {name:32s} {how:18s} {key}")
        print(f"  ... {len(plan)} total. Re-run with --commit to write.")
        return 0

    hardened_backup(args.db, label="add-scholar-urls")
    n = 0
    for key, name, url, how in plan:
        set_person_profiles(conn, person_key=key, profiles={"scholar": {"url": url}})
        n += 1
    conn.commit()
    print(f"COMMITTED {n} scholar URLs to {args.db}")

    # verify
    got = conn.execute(
        "SELECT count(*) FROM nodes WHERE type='Person' AND is_active=1 "
        "AND json_extract(attrs,'$.profiles.scholar.url') IS NOT NULL").fetchone()[0]
    print(f"post-write: {got} people now have a scholar URL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
