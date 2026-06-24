from v2.core.ingestion import ist_crawl


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
