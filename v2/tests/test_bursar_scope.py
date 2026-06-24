from pathlib import Path

from v2.core.ingestion import bursar_crawl as bc

FIX = Path(__file__).parent / "fixtures" / "bursar"
SEED = "https://www.njit.edu/bursar/"


def test_in_scope_is_path_prefix_bound():
    sp = "/bursar/"
    assert bc._in_scope(sp, "/bursar/for-students")
    assert bc._in_scope(sp, "/bursar/1098-t.php")
    assert bc._in_scope(sp, "/bursar/node/71")
    assert not bc._in_scope(sp, "/registrar/")           # off-path same host
    assert not bc._in_scope(sp, "/graduatestudies/")


def test_real_homepage_reaches_key_sections_and_stays_in_scope():
    home = (FIX / "home.html").read_text(encoding="utf-8")
    stub = '<html><body><div role="main"><h1>x</h1>body</div></body></html>'
    seen = []

    def fetch(u):
        seen.append(u)
        return home if u == SEED else stub

    list(bc.crawl_entry(SEED, fetch, max_depth=2, budget=400))
    # sections the homepage LINKS (discovery layer — before any HTTP redirect; the live fetcher
    # redirects /bursar/faqs → /bursar/faq, but the homepage anchor is /bursar/faqs).
    for sec in ("/bursar/for-students",
                "/bursar/forms",
                "/bursar/faqs",
                "/bursar/important-dates",
                "/bursar/touchnet-erefund",
                "/bursar/contact-us"):
        assert f"https://www.njit.edu{sec}" in seen, f"homepage did not reach {sec}"
    assert all("/bursar" in p for p in seen)             # never left the entry point
