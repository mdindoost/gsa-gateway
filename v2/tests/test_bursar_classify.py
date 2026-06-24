from pathlib import Path

from v2.core.ingestion import bursar_crawl as bc

FIX = Path(__file__).parent / "fixtures" / "bursar"


def test_classify():
    # contact-us has no personnel block → it is PROSE for bursar (not staff-roster)
    assert bc.classify_page((FIX / "contact-us.html").read_text(encoding="utf-8")) == "prose"
    assert bc.classify_page((FIX / "for-students.html").read_text(encoding="utf-8")) == "prose"
    # no real bursar page is a JS shell → a SYNTHETIC empty fixture exercises the skip branch
    assert bc.classify_page("<html><body><div role='main'></div></body></html>") == "skip-empty"


def test_budget_truncation_flag():
    def fetch(u):
        n = u.rstrip("/").rsplit("/", 1)[-1]
        cur = int(n) if n.isdigit() else 0
        nxt = f"/bursar/{cur + 1}"                        # stays under the seed path-prefix
        return f'<html><body><div role="main"><h1>{cur}</h1>x</div><a href="{nxt}">n</a></body></html>'

    stats = {}
    list(bc.crawl_entry("https://www.njit.edu/bursar/", fetch, max_depth=99, budget=5, stats=stats))
    assert stats["truncated"] is True
