from pathlib import Path

from v2.core.ingestion import registrar_crawl as rc

FIX = Path(__file__).parent / "fixtures" / "registrar"
SEED = "https://www.njit.edu/registrar/"


def test_staff_page_yields_both_roster_and_prose_coverage_rule():
    """COVERAGE RULE: the staff directory is parsed for KG people AND kept as prose (the page's
    own directory text is served verbatim too — never dropped just because it had a roster)."""
    staff = (FIX / "staff.html").read_text(encoding="utf-8")
    # minimal home that links ONLY to the staff directory (keeps the DFS focused for the test)
    home = ('<html><body><div role="main"><h1>Office of the Registrar</h1>welcome'
            '<a href="/registrar/directory/mallstaff.php">staff</a></div></body></html>')
    pages = {SEED: home, "https://www.njit.edu/registrar/directory/mallstaff.php": staff}

    def fetch(u):
        return pages.get(u)

    res = rc.extract_entry(SEED, fetch, max_depth=2, budget=20)
    assert len(res.staff) == 13
    staff_urls = [p.source_url for p in res.prose if "mallstaff" in p.source_url]
    assert staff_urls, "staff page must also be kept as prose (coverage rule)"


def test_normal_page_yields_verbatim_prose():
    text = "Verbatim withdrawal policy body."
    html = f'<html><body><div role="main"><h1>Withdrawal</h1>{text}</div></body></html>'
    pp = rc.extract_prose("https://www.njit.edu/registrar/withdrawal", html)
    assert pp is not None
    assert pp.title == "Withdrawal"
    assert text in pp.content
