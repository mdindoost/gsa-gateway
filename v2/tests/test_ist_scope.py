from pathlib import Path

from v2.core.ingestion import ist_crawl

FIX = Path(__file__).parent / "fixtures" / "ist"


def test_in_scope_is_host_bound():
    h = "ist.njit.edu"
    # sibling pages under different path prefixes are IN scope (the EOS bug fixed)
    assert ist_crawl._in_scope(h, "https://ist.njit.edu/software-available-download")
    assert ist_crawl._in_scope(h, "https://ist.njit.edu/password-resets")
    assert ist_crawl._in_scope(h, "https://ist.njit.edu/ist-key-contacts")
    # off-host links are OUT
    assert not ist_crawl._in_scope(h, "https://www.njit.edu/registrar/")
    assert not ist_crawl._in_scope(h, "https://servicedesk.njit.edu/x")
    assert not ist_crawl._in_scope(h, "https://myucid.njit.edu/")


def test_crawl_entry_follows_siblings_and_stays_on_host():
    # Fake fetcher over a tiny site; homepage links a sibling + an off-host page.
    home = ('<html><body><a href="/password-resets">pw</a>'
            '<a href="https://www.njit.edu/registrar/">off</a></body></html>')
    pw = '<html><body><div role="main"><h1>Password Resets</h1>Reset your UCID here.</div></body></html>'
    pages = {"https://ist.njit.edu/": home, "https://ist.njit.edu/password-resets": pw}
    seen = []

    def fetch(u):
        seen.append(u)
        return pages.get(u)

    list(ist_crawl.crawl_entry("https://ist.njit.edu/", fetch, max_depth=2, budget=10))
    assert "https://ist.njit.edu/password-resets" in seen   # sibling followed
    assert "https://www.njit.edu/registrar/" not in seen     # off-host dropped


def test_real_homepage_reaches_every_key_section():
    """The whole scope strategy rests on one claim from the pilot (spec §Architecture):
    a single homepage seed + host-scoped DFS exposes every key IST section at depth 1.
    Exercise it against the REAL saved ist.njit.edu homepage, not a stub — if NJIT's nav
    links ever move out of select_links' reach, the live crawl would silently under-collect
    (the EOS path-prefix failure mode). Fetcher returns the real homepage for the seed and a
    minimal valid page for every followed link, so `seen` is exactly the followed set."""
    home = (FIX / "home.html").read_text(encoding="utf-8")
    stub = '<html><body><div role="main"><h1>x</h1>body</div></body></html>'

    seen = []

    def fetch(u):
        seen.append(u)
        return home if u == "https://ist.njit.edu/" else stub

    list(ist_crawl.crawl_entry("https://ist.njit.edu/", fetch, max_depth=2, budget=400))

    # The key student-facing sections the bot must be able to answer from.
    for section in ("/software-available-download", "/password-resets",
                    "/student-computers", "/ist-services", "/ist-key-contacts"):
        assert f"https://ist.njit.edu{section}" in seen, f"homepage did not reach {section}"
    # And the DFS never wandered off the IST subdomain.
    assert all(u.startswith("https://ist.njit.edu") for u in seen)
