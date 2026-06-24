"""TDD: a non-directory office seed (e.g. /environmentalsafety, /about/transportation-campus)
must scope to its OWN path prefix, not the shared crawler's PARENT-dir scope (which resolves
to "/" or "/about/" and crawls the whole university). Caught by the full dry-run.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.eos_crawl import extract_entry


def _page(h1, links):
    a = "".join(f'<a href="{u}">{t}</a>' for u, t in links)
    return f'<div role="main"><h1>{h1}</h1><p>{h1} body text</p>{a}</div>'


def test_leaf_seed_stays_in_its_own_subtree():
    seed = "https://www.njit.edu/environmentalsafety"
    pages = {
        seed: _page("EHS", [("/bursar", "Tuition"),                     # off-scope (global nav)
                            ("/research/faqs/", "Research FAQ"),         # off-scope
                            ("/environmentalsafety/chemical", "Chem")]), # in-scope subtree
        "https://www.njit.edu/environmentalsafety/chemical": _page("Chemical Safety", []),
        "https://www.njit.edu/bursar": _page("Bursar", []),
        "https://www.njit.edu/research/faqs/": _page("Research FAQs", []),
    }
    urls = {p.source_url for p in extract_entry(seed, lambda u: pages.get(u)).prose}
    assert any(u.endswith("/environmentalsafety") for u in urls)
    assert any(u.endswith("/environmentalsafety/chemical") for u in urls)
    assert not any("/bursar" in u for u in urls)
    assert not any("/research/" in u for u in urls)


def test_single_about_page_does_not_grab_whole_about_section():
    seed = "https://www.njit.edu/about/transportation-campus"
    pages = {
        seed: _page("Public Transportation", [("/about/administration", "Admin"),
                                              ("/about/history-njit", "History")]),
        "https://www.njit.edu/about/administration": _page("Administration", []),
        "https://www.njit.edu/about/history-njit": _page("History", []),
    }
    urls = {p.source_url for p in extract_entry(seed, lambda u: pages.get(u)).prose}
    assert urls == {"https://www.njit.edu/about/transportation-campus"}
