from pathlib import Path

from v2.core.ingestion import gradstudies_crawl as gc

FIX = Path(__file__).parent / "fixtures" / "gradstudies"
SEED = "https://www.njit.edu/graduatestudies/"


def test_in_scope_is_path_prefix_bound():
    sp = "/graduatestudies/"
    assert gc._in_scope(sp, "/graduatestudies/forms")
    assert gc._in_scope(sp, "/graduatestudies/content/new-phd-credit-requirements")
    assert not gc._in_scope(sp, "/parking/")          # off-path same host
    assert not gc._in_scope(sp, "/registrar/")


def test_real_homepage_reaches_every_key_section_and_stays_in_scope():
    home = (FIX / "home.html").read_text(encoding="utf-8")
    stub = '<html><body><div role="main"><h1>x</h1>body</div></body></html>'
    seen = []

    def fetch(u):
        seen.append(u)
        return home if u == SEED else stub

    list(gc.crawl_entry(SEED, fetch, max_depth=2, budget=400))
    for sec in ("/graduatestudies/forms",
                "/graduatestudies/current-students",
                "/graduatestudies/degree-programs",
                "/graduatestudies/graduate-faculty",
                "/graduatestudies/full-time-status-phd-students",
                "/graduatestudies/content/new-phd-credit-requirements"):
        assert f"https://www.njit.edu{sec}" in seen, f"homepage did not reach {sec}"
    assert all("/graduatestudies" in p for p in seen)   # never left the entry point
