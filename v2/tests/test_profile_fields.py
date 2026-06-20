from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from v2.core.people.profile_fields import (
    PROFILE_FIELDS, Field, Metric, render_links, render_metrics,
    metric_fields, match_metric, match_link_field,
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


def test_render_metrics_only_renders_one_metric():
    attrs = {"profiles": {"scholar": {
        "citations": 5021, "h_index": 30, "i10_index": 62, "updated_at": "2026-06"}}}
    assert render_metrics(attrs, only="citations") == "Google Scholar: 5,021 citations — as of 2026-06"
    assert render_metrics(attrs, only="h_index") == "Google Scholar: h-index 30 — as of 2026-06"


def test_render_metrics_only_missing_metric_is_none():
    attrs = {"profiles": {"scholar": {"citations": 5021}}}
    assert render_metrics(attrs, only="h_index") is None


def test_metric_fields_lists_every_metric_with_its_field_key():
    mf = metric_fields()
    keys = {(fk, m.key) for fk, m in mf}
    assert keys == {("scholar", "citations"), ("scholar", "h_index"), ("scholar", "i10_index")}


def test_match_metric_hits_each_kept_alias():
    assert match_metric("koutis citations")[1].key == "citations"
    assert match_metric("Koutis citation")[1].key == "citations"
    assert match_metric("how many times has koutis been cited")[1].key == "citations"
    assert match_metric("what is koutis's h-index")[1].key == "h_index"
    assert match_metric("koutis h index")[1].key == "h_index"
    assert match_metric("koutis hindex")[1].key == "h_index"
    assert match_metric("koutis i10-index")[1].key == "i10_index"
    assert match_metric("koutis i10 index")[1].key == "i10_index"
    # returns the field_key too
    assert match_metric("koutis citations")[0] == "scholar"


def test_match_metric_drops_dangerous_aliases():
    # bare "i10" matches immigration forms; "cite" is the verb form, not a metric.
    assert match_metric("do I need form i10 for my visa") is None
    assert match_metric("how do I cite a paper") is None
    assert match_metric("cite your sources") is None


def test_match_metric_none_for_non_metric_text():
    assert match_metric("who is koutis") is None
    assert match_metric("the i1000 sensor reading") is None  # word-boundary: i1000 != i10


def test_match_link_field_hits_each_field():
    assert match_link_field("oria linkedin")[0] == "linkedin"
    assert match_link_field("oria linked in")[0] == "linkedin"
    assert match_link_field("vincent oria google scholar")[0] == "scholar"
    assert match_link_field("oria scholar")[0] == "scholar"
    assert match_link_field("koutis github")[0] == "github"
    assert match_link_field("koutis orcid")[0] == "orcid"
    assert match_link_field("oria website")[0] == "website"
    assert match_link_field("oria homepage")[0] == "website"


def test_match_link_field_none_for_non_link_text():
    assert match_link_field("who is koutis") is None
    assert match_link_field("koutis citations") is None   # metric, not a link


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
