#!/usr/bin/env python
"""One-time backfill: populate metadata.areas on existing active research_areas items
that don't carry it yet (legacy rows from before decompose wrote it natively).

Areas are recovered from the row's stored content via the canonical splitter (see
scripts/_area_tag_migrate.canonical_areas), so this agrees exactly with ingestion and
with the repair script — a ';' inside parens does not fragment an area, and prose /
single-token content honestly yields []. DEFAULT IS A DRY RUN; --commit writes
(hardened, integrity-checked backup first). Going forward decompose writes natively.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import canonical_areas, run_area_migration


def area_tags_from_content(content: str) -> list[str]:
    """Back-compat alias for the canonical area derivation (one definition, shared)."""
    return canonical_areas(content)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true", help="write changes (else dry run)")
    args = ap.parse_args()
    # Backfill scope: rows that currently carry no areas but have a recoverable list.
    return run_area_migration(args.db, args.commit, "pre-areas-backfill",
                              needs=lambda old, new: not old and bool(new))


if __name__ == "__main__":
    raise SystemExit(main())
