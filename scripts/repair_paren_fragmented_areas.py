#!/usr/bin/env python
"""One-time repair: regroup research_areas tags that were fragmented on commas/semicolons
inside parentheses by the pre-2026-06-14 splitter (e.g. 'Machine Learning (Statistical
Learning' + 'Kernel Methods' + 'Similarity Measures)' → one area).

The source bug is fixed in njit_adapter._split_areas (now paren-aware), so this only
repairs existing rows; future ingests are correct. Each affected row's metadata.areas is
re-derived through the same canonical path the backfill uses (scripts/_area_tag_migrate),
so the two tools agree by construction. DEFAULT IS A DRY RUN; --commit writes (hardened,
integrity-checked backup first).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import canonical_areas, run_area_migration


def has_unbalanced(areas: list[str]) -> bool:
    """True if any area has mismatched parens — the fingerprint of comma/semicolon-in-paren
    fragmentation (a real area always has balanced parens)."""
    return any(a.count("(") != a.count(")") for a in areas)


def rederive_areas(content: str) -> list[str]:
    """Back-compat alias for the canonical area derivation (one definition, shared)."""
    return canonical_areas(content)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true", help="write changes (else dry run)")
    args = ap.parse_args()
    # Repair scope: rows whose current areas show paren-fragmentation.
    return run_area_migration(args.db, args.commit, "pre-paren-repair",
                              needs=lambda old, new: has_unbalanced(old))


if __name__ == "__main__":
    raise SystemExit(main())
