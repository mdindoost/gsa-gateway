"""Disciplined personal-site crawler: relevance-gated, same-domain, bounded."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.ingestion.web_crawler import (crawl_site, is_non_html, is_relevant,
                                           is_safe_url, normalize_url, same_scope,
                                           same_site, scope_prefix, select_links)


def test_ssrf_guard_blocks_internal_and_nonhttp():
    # public hosts ok; internal/loopback/link-local/metadata + non-http rejected
    assert is_safe_url("https://example.com/")
    assert not is_safe_url("http://169.254.169.254/latest/meta-data/")  # cloud metadata
    assert not is_safe_url("http://127.0.0.1:8080/")
    assert not is_safe_url("http://localhost/")
    assert not is_safe_url("http://10.0.0.5/")
    assert not is_safe_url("file:///etc/passwd")
    assert not is_safe_url("ftp://example.com/")


def test_same_site_ignores_www_and_blocks_offsite():
    assert same_site("http://web.njit.edu/~x/", "http://web.njit.edu/pubs.html")
    assert same_site("http://www.koutis.org/", "http://koutis.org/research")
    assert not same_site("http://web.njit.edu/~x/", "https://doi.org/10.1/abc")


def test_scope_prefix_and_same_scope_keep_to_personal_path():
    # /~user on a shared host -> scope to that user dir, NOT the whole department host
    assert scope_prefix("http://cs.njit.edu/~crix") == "/~crix/"
    assert same_scope("http://cs.njit.edu/~crix", "http://cs.njit.edu/~crix/pubs.html")
    assert not same_scope("http://cs.njit.edu/~crix", "http://cs.njit.edu/about")
    assert not same_scope("http://cs.njit.edu/~crix", "http://cs.njit.edu/cs-faculty-and-staff")
    # a personal domain seeds at root -> whole host is in scope
    assert scope_prefix("https://www.jamiepayton.com/") == "/"
    assert same_scope("https://jamiepayton.com/", "https://jamiepayton.com/research")


def test_normalize_url_resolves_and_canonicalizes():
    base = "http://web.njit.edu/~x/index.html"
    assert normalize_url("pubs.html", base) == "http://web.njit.edu/~x/pubs.html"
    # trailing slash kept (directory) so relative links resolve correctly
    assert normalize_url("research/", base) == "http://web.njit.edu/~x/research/"
    # fragment + query dropped, host lowercased
    assert normalize_url("Pubs.html?sort=year#top", base) == "http://web.njit.edu/~x/Pubs.html"


def test_directory_seed_relative_links_resolve_under_dir():
    # the classic gotcha: a '/~ikoutis/' dir seed must resolve 'pubs.html' UNDER it
    assert normalize_url("pubs.html", "http://web.njit.edu/~ikoutis/") == \
        "http://web.njit.edu/~ikoutis/pubs.html"


def test_relevance_and_non_html():
    assert is_relevant("My Publications", "http://x/pubs")
    assert is_relevant("", "http://x/research/projects.html")
    assert not is_relevant("Home", "http://x/index.html")
    assert is_non_html("http://x/cv.pdf") and not is_non_html("http://x/cv.html")


HOME = """<html><body>
  <a href="publications.html">Publications</a>
  <a href="research.html">Research projects</a>
  <a href="cv.pdf">Curriculum Vitae</a>
  <a href="blog.html">My Blog</a>                <!-- irrelevant: dropped -->
  <a href="https://twitter.com/prof">Twitter</a> <!-- off-site: dropped -->
  <a href="https://doi.org/10.1/x">A paper DOI</a>  <!-- off-site: dropped -->
  <a href="mailto:p@njit.edu">email</a>          <!-- non-http: dropped -->
</body></html>"""


def test_select_links_follows_relevant_records_pdf_drops_rest():
    follow, files = select_links(HOME, "http://x.edu/index.html", "http://x.edu/")
    assert follow == {"http://x.edu/publications.html", "http://x.edu/research.html"}
    assert files == {"http://x.edu/cv.pdf"}          # CV recorded, not followed


def test_crawl_bfs_respects_depth_budget_and_dedup():
    pages = {
        "http://x.edu/": '<a href="research.html">Research</a><a href="pubs.html">Publications</a>',
        "http://x.edu/research.html": '<a href="proj.html">Project details</a><a href="pubs.html">Publications</a>',
        "http://x.edu/pubs.html": "<p>paper list</p>",
        "http://x.edu/proj.html": "<p>a project</p>",
    }
    res = crawl_site("http://x.edu/", lambda u: pages.get(u), max_depth=2, budget=15)
    got = {p.url for p in res.pages}
    # depth0 home -> depth1 research+pubs -> depth2 proj. pubs reached once (dedup).
    assert got == {"http://x.edu/", "http://x.edu/research.html",
                   "http://x.edu/pubs.html", "http://x.edu/proj.html"}
    assert {p.depth for p in res.pages if p.url.endswith("/proj.html")} == {2}


def test_depth_one_stops_before_second_hop():
    pages = {
        "http://x.edu/": '<a href="research.html">Research</a>',
        "http://x.edu/research.html": '<a href="proj.html">Project details</a>',
        "http://x.edu/proj.html": "<p>deep</p>",
    }
    res = crawl_site("http://x.edu/", lambda u: pages.get(u), max_depth=1, budget=15)
    assert {p.url for p in res.pages} == {"http://x.edu/", "http://x.edu/research.html"}


def test_budget_backstop_records_note():
    # a homepage linking to many relevant pages; budget caps total fetched
    links = "".join(f'<a href="paper{i}.html">Paper {i} research</a>' for i in range(20))
    pages = {"http://x.edu/": links}
    pages.update({f"http://x.edu/paper{i}.html": "<p>x</p>" for i in range(20)})
    res = crawl_site("http://x.edu/", lambda u: pages.get(u), max_depth=2, budget=5)
    assert len(res.pages) == 5
    assert "budget" in res.note
