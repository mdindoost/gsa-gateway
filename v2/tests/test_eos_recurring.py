"""TDD: drop ONLY site-wide near-universal chrome assets (e.g. the Late Night Lyft PDF on
nearly every page). Per the 2026-06-23 verbatim hard line, an asset on a MINORITY of pages
(a real form/rate-sheet shared by a few pages) must NEVER be stripped. Threshold: an asset
is chrome only when it appears on >= n-1 of n pages AND n >= 5 (small crawls strip nothing).
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.eos_crawl import ProsePage, _strip_recurring_assets

LYFT = ("https://x/lyft.pdf", "Lyft")
FORM = ("https://x/form.pdf", "Form")


def _pg(i, files):
    return ProsePage(f"T{i}", f"body {i}", f"https://x/p{i}", files=tuple(files))


def test_strips_near_universal_keeps_minority():
    pages = [_pg(i, [LYFT] + ([FORM] if i < 3 else [])) for i in range(6)]  # lyft 6/6, form 3/6
    _strip_recurring_assets(pages)
    allf = {u for p in pages for u, _ in p.files}
    assert "https://x/lyft.pdf" not in allf       # near-universal chrome -> stripped
    assert "https://x/form.pdf" in allf            # minority (3/6) legit asset -> KEPT


def test_no_strip_below_five_pages():
    pages = [_pg(i, [LYFT]) for i in range(4)]      # 4 pages, lyft on all
    _strip_recurring_assets(pages)
    assert any("lyft" in u for p in pages for u, _ in p.files)  # n<5 -> nothing stripped


def test_strips_n_minus_one():
    pages = [_pg(i, [LYFT] if i > 0 else []) for i in range(6)]  # lyft on 5/6
    _strip_recurring_assets(pages)
    assert not any("lyft" in u for p in pages for u, _ in p.files)  # >= n-1 -> stripped
