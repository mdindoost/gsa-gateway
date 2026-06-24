"""TDD: drop site-wide RECURRING assets (e.g. the Late Night Lyft PDF that appears in an
announcement block on nearly every page). A page-specific asset (e.g. the campus map) is
kept; an asset appearing on many pages is chrome, removed from all.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.eos_crawl import extract_entry

SEED = "https://www.njit.edu/parking/"


def _page(h1, body, links):
    a = "".join(f'<a href="{u}">{t}</a>' for u, t in links)
    return f'<div role="main"><h1>{h1}</h1><p>{body}</p>{a}</div>'


def _result():
    lyft = ("/parking/files/lyft.pdf", "Late Night Lyft")  # recurring on every page
    pages = {
        SEED: _page("Hub", "hub text", [("/parking/a", "A"), ("/parking/b", "B"), lyft]),
        "https://www.njit.edu/parking/a": _page("A", "alpha text", [lyft, ("/parking/files/a.pdf", "A doc")]),
        "https://www.njit.edu/parking/b": _page("B", "beta text", [lyft, ("/parking/files/b.pdf", "B doc")]),
    }
    return extract_entry(SEED, lambda u: pages.get(u))


def test_recurring_asset_removed_everywhere():
    all_files = {u for p in _result().prose for u, _ in p.files}
    assert not any(u.endswith("/lyft.pdf") for u in all_files)


def test_page_specific_assets_kept():
    all_files = {u for p in _result().prose for u, _ in p.files}
    assert any(u.endswith("/a.pdf") for u in all_files)
    assert any(u.endswith("/b.pdf") for u in all_files)
