from pathlib import Path
from v2.core.ingestion import ist_crawl

FIX = Path(__file__).parent / "fixtures" / "ist"


def test_software_page_extracts_verbatim():
    html = (FIX / "software.html").read_text(encoding="utf-8")
    page = ist_crawl.extract_prose("https://ist.njit.edu/software-available-download", html)
    assert page is not None
    assert page.title == "Software Availability"
    # Verbatim: a sentence literally on the page is present, unaltered.
    assert "IST provides the NJIT community with access to a variety of software" in page.content
    assert ist_crawl.IST_SLUG == "ist"
