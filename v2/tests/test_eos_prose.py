"""TDD for the EOS verbatim prose extractor.

Fixture is the REAL fetched page (visitor_parking.html). The extractor must keep the
page's MAIN content verbatim and drop site chrome (the "Popular Searches" footer +
global mega-menu) — mechanical boilerplate removal only, NO rewriting (hard line #3).
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.eos_crawl import extract_prose

FIXTURE = Path(__file__).parent / "fixtures" / "eos" / "visitor_parking.html"
URL = "https://www.njit.edu/parking/visitor-parking"


def _page():
    return extract_prose(URL, FIXTURE.read_text())


def test_extract_prose_title_and_source():
    p = _page()
    assert p.title == "Visitor Parking"
    assert p.source_url == URL


def test_extract_prose_keeps_main_content_verbatim():
    # A sentence that appears literally on the page must survive unchanged.
    p = _page()
    assert "NJIT faculty and staff who are receiving visitors on campus" in p.content


def test_extract_prose_drops_site_chrome():
    p = _page()
    assert "Popular Searches" not in p.content
    assert "Admission Application" not in p.content


def test_extract_prose_starts_at_heading():
    assert _page().content.startswith("Visitor Parking")


def test_extract_prose_is_exactly_mechanical_clean_no_rewrite():
    # Strong verbatim guard: content must be EXACTLY clean_text of the main region — proves
    # no summarize/paraphrase/reorder/drop ever creeps in (hard line #3). If someone later
    # adds any text transformation to extract_prose, this fails.
    from bs4 import BeautifulSoup
    from v2.core.ingestion.web_crawler import clean_text
    from v2.core.ingestion.eos_crawl import _main_region
    expected = clean_text(str(_main_region(BeautifulSoup(FIXTURE.read_text(), "html.parser"))))
    assert _page().content == expected
