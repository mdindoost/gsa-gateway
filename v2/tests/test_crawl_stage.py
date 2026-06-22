"""Tests for the seed/link-follow staging helper in scripts/_crawl_stage.py.

The EOS/parking pages are NOT in NJIT's sitemap, so the seed mode fetches a hub and follows
same-host links under given path prefixes (depth 1). select_seed_links is the pure helper.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._crawl_stage import select_seed_links

HUB = "https://www.njit.edu/parking/"
FOLLOW = ["/parking/", "/mailroom", "/sustainability", "/about/transportation"]

# Models the real hub: relative + protocol-relative + absolute links, a cross-site same-host
# link (/mailroom/), a fragment, a CSS-include asset, an external host, an unrelated dept page,
# a mailto, and a duplicate.
HUB_HTML = """
<html><body>
  <a href="/parking/visitor-parking">Visitor Parking</a>
  <a href="/parking/visitor-parking">Visitor Parking (dup)</a>
  <a href="//www.njit.edu/about/transportation-campus">Transportation</a>
  <a href="https://www.njit.edu/mailroom/">Mailroom</a>
  <a href="https://www.njit.edu/sustainability/">Office of Sustainability</a>
  <a href="/parking/photo-identification#contact">Photo ID</a>
  <a href="/parking/sites/njit.edu.parking/files/css/style.css?delta=1&amp;language=en">css</a>
  <a href="https://external.example.com/parking/foo">external host</a>
  <a href="/academics/degree/ms-transportation">unrelated dept page</a>
  <a href="mailto:parking@njit.edu">email us</a>
</body></html>
"""


def test_keeps_same_host_links_under_follow_prefixes():
    links = select_seed_links(HUB, HUB_HTML, FOLLOW)
    assert "https://www.njit.edu/parking/visitor-parking" in links
    assert "https://www.njit.edu/about/transportation-campus" in links   # protocol-relative resolved
    assert "https://www.njit.edu/mailroom/" in links                     # cross-site, same host
    assert "https://www.njit.edu/sustainability/" in links
    assert "https://www.njit.edu/parking/photo-identification" in links  # fragment dropped


def test_drops_external_assets_offprefix_and_dedupes():
    links = select_seed_links(HUB, HUB_HTML, FOLLOW)
    assert not any("external.example.com" in l for l in links)           # external host
    assert not any(l.endswith(".css") for l in links)                    # CSS asset
    assert not any("/academics/degree" in l for l in links)              # not under a follow prefix
    assert not any(l.startswith("mailto") for l in links)                # mailto
    assert links.count("https://www.njit.edu/parking/visitor-parking") == 1  # dedupe


def test_follow_prefix_is_anchored_not_substring():
    # A path that merely CONTAINS "/parking" mid-string must not match the "/parking/" prefix.
    html = '<a href="/research/parking-study">parking research</a>'
    assert select_seed_links(HUB, html, ["/parking/"]) == []


def test_slug_from_url_path():
    from scripts._crawl_stage import _slug
    assert _slug("eos", "https://www.njit.edu/parking/visitor-parking") == "eos__parking-visitor-parking"
    assert _slug("eos", "https://www.njit.edu/parking/") == "eos__parking"


def test_stage_skips_empty_bodies_and_writes_source_header(tmp_path):
    from scripts._crawl_stage import stage
    pages = {
        "https://x.njit.edu/full": "<html><body>" + ("word " * 100) + "</body></html>",
        "https://x.njit.edu/shell": "<html><body><div id='app'></div></body></html>",  # JS-only
    }
    man = stage(["https://x.njit.edu/full", "https://x.njit.edu/shell", "https://x.njit.edu/missing"],
                fetch=lambda u: pages.get(u),
                slug_of=lambda u: "eos__" + u.rsplit("/", 1)[-1],
                stage_dir=tmp_path, min_chars=50)
    assert {m["slug"] for m in man} == {"eos__full"}            # shell (near-empty) + missing skipped
    body = (tmp_path / "eos__full.txt").read_text(encoding="utf-8")
    assert body.startswith("SOURCE_URL: https://x.njit.edu/full\n\n")


def test_requires_exactly_one_mode():
    import pytest
    from scripts._crawl_stage import main
    with pytest.raises(SystemExit):
        main(["--prefix", "eos"])                                       # neither --bucket nor --seed
    with pytest.raises(SystemExit):
        main(["--prefix", "eos", "--bucket", "/x/", "--seed", "https://x/"])  # both
