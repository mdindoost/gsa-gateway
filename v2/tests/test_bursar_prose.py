from pathlib import Path

from bs4 import BeautifulSoup

from v2.core.ingestion.web_crawler import clean_text
from v2.core.ingestion import bursar_crawl as bc

FIX = Path(__file__).parent / "fixtures" / "bursar"


def test_contact_page_yields_prose_with_office_contacts():
    """contact-us yields NO staff but DOES yield prose carrying the office contacts (coverage
    rule): the general email + phone are served verbatim from the KB."""
    contact = (FIX / "contact-us.html").read_text(encoding="utf-8")
    for_students = (FIX / "for-students.html").read_text(encoding="utf-8")
    contact2 = contact.replace("</body>", '<a href="/bursar/for-students">x</a></body>')
    pages = {
        "https://www.njit.edu/bursar/": contact2,
        "https://www.njit.edu/bursar/for-students": for_students,
    }

    def fetch(u):
        return pages.get(u)

    res = bc.extract_entry("https://www.njit.edu/bursar/", fetch, max_depth=2, budget=20)

    assert res.staff == []                                         # prose-only office
    urls = {p.source_url for p in res.prose}
    assert "https://www.njit.edu/bursar/" in urls                 # contact prose kept
    assert any("bursar@njit.edu" in p.content for p in res.prose)  # office email served


def test_verbatim_prose_unaltered():
    fs = (FIX / "for-students.html").read_text(encoding="utf-8")
    page = bc.extract_prose("https://www.njit.edu/bursar/for-students", fs)
    assert page is not None and page.content
    # content is exactly the mechanical clean of the main region — no rewriting
    assert page.content in clean_text(str(BeautifulSoup(fs, "html.parser")))
