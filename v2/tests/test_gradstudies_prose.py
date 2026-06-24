from pathlib import Path

from bs4 import BeautifulSoup

from v2.core.ingestion.web_crawler import clean_text
from v2.core.ingestion import gradstudies_crawl as gc

FIX = Path(__file__).parent / "fixtures" / "gradstudies"


def test_contact_page_yields_both_staff_and_prose():
    contact = (FIX / "contact.php.html").read_text(encoding="utf-8")
    phd = (FIX / "phd_credit.html").read_text(encoding="utf-8")
    # tiny self-contained site: contact (staff+prose) links a prose-only page /x
    contact2 = contact.replace("</body>", '<a href="/graduatestudies/x">x</a></body>')
    pages = {
        "https://www.njit.edu/graduatestudies/": contact2,
        "https://www.njit.edu/graduatestudies/x": phd,
    }

    def fetch(u):
        return pages.get(u)

    res = gc.extract_entry("https://www.njit.edu/graduatestudies/", fetch, max_depth=2, budget=20)

    assert any("Ziavras" in s.name for s in res.staff)              # staff captured
    urls = {p.source_url for p in res.prose}
    assert "https://www.njit.edu/graduatestudies/" in urls         # contact prose ALSO kept
    assert any("graduatestudies@njit.edu" in p.content for p in res.prose)  # office email served


def test_verbatim_prose_unaltered():
    phd = (FIX / "phd_credit.html").read_text(encoding="utf-8")
    page = gc.extract_prose(
        "https://www.njit.edu/graduatestudies/content/new-phd-credit-requirements", phd)
    # content is exactly the mechanical clean of the main region — no rewriting
    assert page is not None and page.content
    assert page.content in clean_text(str(BeautifulSoup(phd, "html.parser")))
