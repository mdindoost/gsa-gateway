"""Tests for the JS-discovery pure helpers (v2/core/ingestion/js_discovery.py).

The Playwright render itself is not unit-tested (no browser/network in CI — it's
verified at the spec's §5 live gate). These cover the deterministic logic:
profile extraction, the DOM-vs-intercepted cross-check, and the
Playwright-absent error contract.
"""

import pytest

from v2.core.ingestion.js_discovery import (
    DiscoveryResult,
    _extract_profiles,
    crosscheck,
    discover_js,
)


def test_extract_profiles_finds_unique_in_order():
    html = """
      <a href="https://people.njit.edu/profile/abc1">A</a>
      <a href="https://people.njit.edu/profile/zzz9">Z</a>
      <a href="https://people.njit.edu/profile/abc1">A again</a>
      <a href="/elsewhere">no</a>
    """
    assert _extract_profiles(html) == [
        "https://people.njit.edu/profile/abc1",
        "https://people.njit.edu/profile/zzz9",
    ]


def test_extract_profiles_empty_or_none():
    assert _extract_profiles("") == []
    assert _extract_profiles(None) == []


def test_crosscheck_true_when_same_profiles_different_order():
    dom = ["https://people.njit.edu/profile/a", "https://people.njit.edu/profile/b"]
    api = ["https://people.njit.edu/profile/b", "https://people.njit.edu/profile/a/"]
    assert crosscheck(dom, api) is True


def test_crosscheck_false_when_one_side_is_truncated():
    dom = ["https://people.njit.edu/profile/a", "https://people.njit.edu/profile/b"]
    api = ["https://people.njit.edu/profile/a"]   # missing b (e.g. paginated)
    assert crosscheck(dom, api) is False


def test_discover_js_raises_runtimeerror_without_playwright():
    # Playwright is not a hard dependency; a missing install must raise a clear
    # RuntimeError (NOT SystemExit, or it would abort the whole --all batch).
    with pytest.raises(RuntimeError):
        discover_js("https://ds.njit.edu/people")


def test_discovery_result_shape():
    r = DiscoveryResult(urls=["x"], intercepted=["x"], title="t", html_len=5)
    assert r.urls == ["x"] and r.intercepted == ["x"]
