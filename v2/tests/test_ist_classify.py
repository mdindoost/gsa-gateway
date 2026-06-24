from pathlib import Path
from v2.core.ingestion import ist_crawl

FIX = Path(__file__).parent / "fixtures" / "ist"


def test_classify_real_pages():
    kc = (FIX / "key_contacts.html").read_text(encoding="utf-8")
    sw = (FIX / "software.html").read_text(encoding="utf-8")
    assert ist_crawl.classify_page(kc) == "staff-roster"
    assert ist_crawl.classify_page(sw) == "prose"
    assert ist_crawl.classify_page("<html><body><div role='main'></div></body></html>") == "skip-empty"


def test_budget_truncation_flag():
    # Two-page chain but budget=1 -> truncated True.
    home = '<html><body><a href="/a">a</a></body></html>'
    a = '<html><body><div role="main"><h1>A</h1>text</div></body></html>'
    pages = {"https://ist.njit.edu/": home, "https://ist.njit.edu/a": a}
    res = ist_crawl.extract_entry("https://ist.njit.edu/", lambda u: pages.get(u),
                                  max_depth=2, budget=1)
    assert res.truncated is True
