from pathlib import Path

from v2.core.ingestion import registrar_crawl as rc

FIX = Path(__file__).parent / "fixtures" / "registrar"


def test_classify():
    # the real staff directory (Name/Phone/Functions table) classifies as a roster
    assert rc.classify_page((FIX / "staff.html").read_text(encoding="utf-8")) == "staff-roster"
    # a normal service page is prose
    assert rc.classify_page((FIX / "withdrawal.html").read_text(encoding="utf-8")) == "prose"
    assert rc.classify_page((FIX / "transcript.html").read_text(encoding="utf-8")) == "prose"
    # a JS-only shell with no readable main content → skip-empty
    assert rc.classify_page("<html><body><div role='main'></div></body></html>") == "skip-empty"


def test_budget_truncation_flag():
    def fetch(u):
        n = u.rstrip("/").rsplit("/", 1)[-1]
        cur = int(n) if n.isdigit() else 0
        nxt = f"/registrar/{cur + 1}"                     # stays under the seed path-prefix
        return f'<html><body><div role="main"><h1>{cur}</h1>x</div><a href="{nxt}">n</a></body></html>'

    stats = {}
    list(rc.crawl_entry("https://www.njit.edu/registrar/", fetch, max_depth=99, budget=5, stats=stats))
    assert stats["truncated"] is True
