#!/usr/bin/env python3
"""verify_ds.py — DS faculty discovery verification gate (DS crawler spec §5).

Run this BEFORE setting ds.verified=True in the department registry. It renders
ds.njit.edu/people headless and reports the two-oracle completeness check, so you
can confirm discovery is COMPLETE (not a truncated first page) before enabling DS
in the "Refresh NJIT KB" button.

Usage:  .venv/bin/python scripts/verify_ds.py
Needs:  pip install playwright && playwright install chromium
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from v2.core.ingestion.departments import get
from v2.core.ingestion.js_discovery import _slugs, crosscheck, discover_js


def main() -> int:
    dept = get("ds")
    print(f"Rendering {dept.faculty_list} (headless)…\n")
    try:
        res = discover_js(dept.faculty_list)
    except RuntimeError as exc:
        print(f"✗ {exc}")
        return 1

    dom, inter = res.urls, res.intercepted
    print(f"DOM-scraped profiles    : {len(dom)}")
    print(f"Intercepted (page API)  : {len(inter)}")

    if inter:
        ok = crosscheck(dom, inter)
        print(f"Cross-check (DOM == API): {'✓ MATCH' if ok else '✗ MISMATCH'}")
        if not ok:
            only_dom = sorted(_slugs(dom) - _slugs(inter))
            only_api = sorted(_slugs(inter) - _slugs(dom))
            if only_dom:
                print(f"   only in DOM: {only_dom[:10]}")
            if only_api:
                print(f"   only in API: {only_api[:10]}")
    else:
        print("Cross-check             : (no JSON response intercepted — "
              "rely on the count + spot-check below)")

    slugs = sorted(_slugs(dom))
    print(f"\nProfiles (sorted, {len(slugs)}):")
    for s in slugs:
        print("  ", s)
    if slugs:
        print(f"\nFirst: {slugs[0]}    Last: {slugs[-1]}")
        print("  ↑ confirm the LAST-alphabetical professor is really the last on the "
              "live page (this catches a truncated/paginated list).")

    print("\nPASS criteria before flipping ds.verified=True:")
    print("  1. count matches the live ds.njit.edu/people page")
    print("  2. cross-check MATCH (or, with no intercepted API, count + names confirmed)")
    print("  3. a W/Z-surname professor is present (no truncation)")
    print("  4. dry-run resolves to DS = org 6 (name 'Data Science'):")
    print("       .venv/bin/python scripts/ingest_faculty.py --department ds --limit 2 --overview")
    print("\nThen set verified=True for 'ds' in v2/core/ingestion/departments.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
