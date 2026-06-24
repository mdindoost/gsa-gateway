"""TDD: when the crawl hits its page budget with links still queued, it must FLAG the
truncation (don't silently miss pages) so the CLI manifest can warn.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.eos_crawl import extract_entry

SEED = "https://www.njit.edu/parking/"


def _page(h1, links):
    a = "".join(f'<a href="{u}">{t}</a>' for u, t in links)
    return f'<div role="main"><h1>{h1}</h1><p>{h1} body</p>{a}</div>'


def test_budget_truncation_is_flagged():
    pages = {SEED: _page("Hub", [(f"/parking/p{i}", f"P{i}") for i in range(10)])}
    for i in range(10):
        pages[f"https://www.njit.edu/parking/p{i}"] = _page(f"P{i}", [])
    res = extract_entry(SEED, lambda u: pages.get(u), budget=3)
    assert res.truncated is True


def test_no_truncation_within_budget():
    pages = {SEED: _page("Hub", [("/parking/a", "A")]),
             "https://www.njit.edu/parking/a": _page("A", [])}
    res = extract_entry(SEED, lambda u: pages.get(u))
    assert res.truncated is False
