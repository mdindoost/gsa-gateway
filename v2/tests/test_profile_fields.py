from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from v2.core.people.profile_fields import (
    PROFILE_FIELDS, Field, Metric, render_links, render_metrics,
)


def test_render_links_none_when_empty():
    assert render_links({}) is None
    assert render_links(None) is None
    assert render_links({"profiles": {}}) is None


def test_render_links_lists_present_links_in_registry_order():
    attrs = {"profiles": {
        "linkedin": {"url": "https://linkedin.com/in/koutis"},
        "scholar": {"url": "https://scholar.google.com/x"},
    }}
    out = render_links(attrs)
    # registry order: scholar before linkedin
    assert out.index("Google Scholar") < out.index("LinkedIn")
    assert "[Google Scholar](https://scholar.google.com/x)" in out
    assert "[LinkedIn](https://linkedin.com/in/koutis)" in out
    assert "ORCID" not in out  # absent field not shown


def test_website_alias_reads_crawler_flat_website():
    # No profiles.website, but the crawler's flat attrs.website is present.
    attrs = {"website": "https://koutis.example.edu"}
    out = render_links(attrs)
    assert "[Website](https://koutis.example.edu)" in out


def test_website_prefers_profiles_over_links():
    attrs = {"profiles": {"website": {"url": "https://new.site"}},
             "links": {"website": "https://old.site"}}
    assert "https://new.site" in render_links(attrs)
    assert "https://old.site" not in render_links(attrs)


def test_render_metrics_formats_deterministically():
    attrs = {"profiles": {"scholar": {
        "url": "x", "citations": 5021, "h_index": 30, "i10_index": 62,
        "updated_at": "2026-06"}}}
    out = render_metrics(attrs)
    assert out == "Google Scholar: 5,021 citations, h-index 30, i10-index 62 — as of 2026-06"


def test_render_metrics_none_when_no_numbers():
    assert render_metrics({"profiles": {"scholar": {"url": "x"}}}) is None
    assert render_metrics({}) is None


def test_render_metrics_partial():
    attrs = {"profiles": {"scholar": {"citations": 12}}}
    assert render_metrics(attrs) == "Google Scholar: 12 citations"


def test_adding_a_field_is_one_row():
    # The extensibility promise: a new link field appears in render_links with no
    # renderer change, purely from being in the registry.
    extra = Field("mastodon", "Mastodon", "🐘")
    attrs = {"profiles": {"mastodon": {"url": "https://m.social/@k"}}}
    import v2.core.people.profile_fields as pf
    original = pf.PROFILE_FIELDS
    try:
        pf.PROFILE_FIELDS = original + (extra,)
        assert "[Mastodon](https://m.social/@k)" in render_links(attrs)
    finally:
        pf.PROFILE_FIELDS = original
