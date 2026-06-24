from pathlib import Path

from v2.core.ingestion import gradstudies_crawl as gc

FIX = Path(__file__).parent / "fixtures" / "gradstudies"


def test_classify():
    assert gc.classify_page((FIX / "contact.php.html").read_text(encoding="utf-8")) == "staff-roster"
    assert gc.classify_page((FIX / "phd_credit.html").read_text(encoding="utf-8")) == "prose"
    assert gc.classify_page("<html><body><div role='main'></div></body></html>") == "skip-empty"


def test_budget_truncation_flag():
    # a chain of distinct child pages UNDER the seed dir (in-scope) longer than the budget
    # sets truncated=True
    def fetch(u):
        n = u.rstrip("/").rsplit("/", 1)[-1]
        cur = int(n) if n.isdigit() else 0
        nxt = f"/graduatestudies/{cur + 1}"           # stays under the seed path-prefix
        return f'<html><body><div role="main"><h1>{cur}</h1>x</div><a href="{nxt}">n</a></body></html>'

    stats = {}
    list(gc.crawl_entry("https://www.njit.edu/graduatestudies/", fetch,
                        max_depth=99, budget=5, stats=stats))
    assert stats["truncated"] is True
