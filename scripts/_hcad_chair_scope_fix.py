#!/usr/bin/env python
"""One-off gated fix: put the Hillier College (HCAD) chairships on the units actually chaired,
and demote a cross-listed chair's borrowed home edge.

WHY (verified against NJIT on 2026-08-26, not inferred):
  "who is the chair of New Jersey School of Architecture" answered with TWO people — Gernot
  Riether AND Maurie Cohen. design.njit.edu/our-people lists Riether as Chair of the Department
  of Architecture and Mathew Schwartz as Chair of the Department of Art + Design, while Cohen
  appears only under the "Architecture Faculty" section. Cohen's own profile reads "Chair,
  [College of Science and Liberal Arts] ... Chair of the Department of Humanities and Social
  Sciences" plus "Joint appointment ... the Hillier College of Architecture and Design".
  NJIT profile headings render "<role>, <division the person sits in>" — NOT the unit chaired
  (Esperdy's "Dean Architecture, Provost & Academic Affairs" is the tell) — which is why the
  crawler cannot tell from a card alone which unit a bare "Chair" belongs to.

ONE deterministic correction:
  DEMOTE Cohen's NJSOA home edge faculty -> joint (NJIT calls it a joint appointment). The
     affiliation is PRESERVED (is_active=1, still in people_in_org, now marked); it simply stops
     answering "who is the chair of NJSOA" — which the 2026-08-26 non-home role attribution fix
     (entity.NON_HOME_CATEGORIES) enforces once the category is right.

⚠️ NOT DURABLE ON ITS OWN: `scripts/run_explore.py` re-creates both shapes from the HCAD listing
   (Leadership -> hcad, Architecture Faculty -> njsoa/faculty). Re-run this after every crawl, or
   land the producer fix (the deferred home-appointment-cap item in
   docs/superpowers/specs/2026-07-05-affiliated-faculty-category-design.md).

Dry-run by default; --commit takes a hardened_backup first. Idempotent (re-run = 0 changes).
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from scripts._area_tag_migrate import hardened_backup            # noqa: E402

DB = str(REPO / "gsa_gateway.db")

# (person name, from-org slug, to-org slug, the title that must be on the edge)
# DELIBERATELY EMPTY. Re-filing Riether's/Schwartz's HCAD 'Leadership' Chair edges onto the schools
# they actually chair (verified: Riether -> Department of Architecture, Schwartz -> Art + Design) was
# designed and then CUT: both already hold a faculty edge on the destination school, so the only
# non-destructive merge would flip that edge to category='admin' and silently drop them from
# `faculty_in_department` ("who are the NJSOA faculty"). Their school-level chair answers are already
# CORRECT via those faculty edges. What stays wrong is "who is the chair of Hillier College" (returns
# both) — that is the college-level-title class scoped out of the 2026-08-26 spec and needs its own
# design, not a lossy edge move here.
REFILE: list = []
# (person name, org slug, from-category, to-category)
DEMOTE = [("Cohen, Maurie", "njsoa", "faculty", "joint")]


def _org_node(conn, slug):
    row = conn.execute(
        "SELECT n.id FROM nodes n JOIN organizations o ON o.id=json_extract(n.attrs,'$.org_id') "
        "WHERE n.type='Org' AND o.slug=?", (slug,)).fetchone()
    return row[0] if row else None


def _person(conn, name):
    row = conn.execute("SELECT id FROM nodes WHERE type='Person' AND name=? AND is_active=1",
                       (name,)).fetchone()
    return row[0] if row else None


def plan(conn):
    """Return (refiles, demotes, skips) — each entry fully resolved against the live DB."""
    refiles, demotes, skips = [], [], []
    for name, src_slug, dst_slug, title in REFILE:
        pid, src, dst = _person(conn, name), _org_node(conn, src_slug), _org_node(conn, dst_slug)
        if not all((pid, src, dst)):
            skips.append(f"{name}: person/org node missing ({src_slug}->{dst_slug})")
            continue
        row = conn.execute("SELECT id, attrs, category FROM edges WHERE src_id=? AND type='has_role'"
                           " AND dst_id=? AND is_active=1", (pid, src)).fetchone()
        if not row:
            skips.append(f"{name}: no active has_role edge on {src_slug} (already re-filed?)")
            continue
        titles = (json.loads(row[1]) if row[1] else {}).get("titles") or []
        if title not in titles:
            skips.append(f"{name}: {src_slug} edge titles {titles} do not carry {title!r} — SKIP")
            continue
        # never clobber an existing edge on the destination
        existing = conn.execute("SELECT id FROM edges WHERE src_id=? AND type='has_role' AND dst_id=?",
                                (pid, dst)).fetchone()
        refiles.append((name, row[0], src_slug, dst_slug, dst, titles, row[2],
                        existing[0] if existing else None))
    for name, slug, frm, to in DEMOTE:
        pid, org = _person(conn, name), _org_node(conn, slug)
        if not all((pid, org)):
            skips.append(f"{name}: person/org node missing ({slug})")
            continue
        row = conn.execute("SELECT id, category FROM edges WHERE src_id=? AND type='has_role'"
                           " AND dst_id=? AND is_active=1", (pid, org)).fetchone()
        if not row:
            skips.append(f"{name}: no active has_role edge on {slug}")
            continue
        if row[1] != frm:
            skips.append(f"{name}: {slug} edge is category={row[1]!r}, expected {frm!r} — SKIP")
            continue
        demotes.append((name, row[0], slug, frm, to))
    return refiles, demotes, skips


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    conn = sqlite3.connect(a.db)
    conn.execute("PRAGMA foreign_keys=ON")
    refiles, demotes, skips = plan(conn)

    print("RE-FILE a chairship onto the unit actually chaired:")
    for name, eid, src, dst, _dstid, titles, cat, clash in refiles:
        flag = "  ⚠️ destination edge EXISTS — will MERGE titles" if clash else ""
        print(f"  {name:18} edge {eid}: {src} -> {dst}   category={cat} titles={titles}{flag}")
    print("\nDEMOTE a cross-listed home edge (affiliation preserved, is_active stays 1):")
    for name, eid, slug, frm, to in demotes:
        print(f"  {name:18} edge {eid}: {slug}  category {frm} -> {to}")
    if skips:
        print("\nSKIPPED (guard tripped — nothing written for these):")
        for s in skips:
            print(f"  {s}")
    total = len(refiles) + len(demotes)
    print(f"\n=> {total} edge(s) would change.")
    if not a.commit:
        print("\n(dry run — pass --commit to apply; a hardened backup is taken first)")
        return
    if not total:
        print("nothing to do.")
        return
    hardened_backup(a.db, "hcad_chair_scope")
    with conn:
        for name, eid, _src, dst_slug, dstid, titles, cat, clash in refiles:
            if clash:
                old = conn.execute("SELECT attrs FROM edges WHERE id=?", (clash,)).fetchone()[0]
                merged = json.loads(old) if old else {}
                merged["titles"] = list(dict.fromkeys((merged.get("titles") or []) + titles))
                conn.execute("UPDATE edges SET attrs=?, category=?, is_active=1 WHERE id=?",
                             (json.dumps(merged), cat, clash))
                conn.execute("UPDATE edges SET is_active=0 WHERE id=?", (eid,))
            else:
                conn.execute("UPDATE edges SET dst_id=? WHERE id=?", (dstid, eid))
        for _name, eid, _slug, _frm, to in demotes:
            conn.execute("UPDATE edges SET category=? WHERE id=?", (to, eid))
    print(f"✓ committed {total} edge change(s).")


if __name__ == "__main__":
    main()
