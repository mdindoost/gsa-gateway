#!/usr/bin/env python
"""Grounding filter for crawled-then-extracted njit-web docs (the safety net).

A cheap model (Haiku subagent) extracts candidate fact-lines from a staged NJIT page; this
script keeps ONLY lines that appear VERBATIM (whitespace-normalized substring) in the staged
raw page text, and drops the rest. So even a smarter model cannot inject a fact that is not
literally on the page. Docs left with fewer than MIN_FACTS grounded lines are removed (those
pages -> the live fallback). Front-matter and `#`/`##` headings are preserved.

Usage: python scripts/_crawl_ground_filter.py            # report only
       python scripts/_crawl_ground_filter.py --apply    # rewrite docs in place + drop empties
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "bot" / "data" / "sources" / "njit-web"
STAGE = Path("/tmp/njit_crawl")
MIN_FACTS = 3


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def filter_doc(doc: Path, raw: str) -> tuple[list[str], int, int]:
    """Return (new_lines, kept, total_body) keeping only verbatim body lines."""
    rawn = _norm(raw)
    out: list[str] = []
    in_fm = False
    kept = total = 0
    for ln in doc.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s == "---":
            in_fm = not in_fm
            out.append(ln)
            continue
        if in_fm or ln.startswith("#") or not s:
            out.append(ln)
            continue
        body = s.lstrip("-* ").strip()
        total += 1
        if len(body) >= 12 and _norm(body) in rawn:
            out.append(ln)
            kept += 1
    return out, kept, total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    total_kept = 0
    for doc in sorted(DOCS.glob("*.md")):
        raw_path = STAGE / (doc.stem + ".txt")
        if not raw_path.exists():
            print(f"  SKIP {doc.name}: no staged source")
            continue
        new_lines, kept, total = filter_doc(doc, raw_path.read_text(encoding="utf-8"))
        total_kept += kept
        verdict = "DROP (too few facts)" if kept < MIN_FACTS else f"keep {kept}"
        print(f"  {doc.name}: {kept}/{total} verbatim -> {verdict}")
        if args.apply:
            if kept < MIN_FACTS:
                doc.unlink()
            else:
                doc.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"\ntotal grounded fact-lines kept: {total_kept}"
          + ("  (applied)" if args.apply else "  (dry run — pass --apply)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
