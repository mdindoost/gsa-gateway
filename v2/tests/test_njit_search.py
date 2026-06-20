import json
from v2.integration.njit_search import search

BRAVE_JSON = json.dumps({"web": {"results": [
    {"url": "https://www.njit.edu/registrar/registration"},
    {"url": "https://catalog.njit.edu/x"},
    {"url": "https://evil.example.com/x"},
]}})


def test_returns_njit_urls_only():
    got = search("how do I register", k=3, http_get=lambda url, headers: BRAVE_JSON, key="K")
    assert got == ["https://www.njit.edu/registrar/registration", "https://catalog.njit.edu/x"]


def test_empty_without_key():
    assert search("x", http_get=lambda url, headers: BRAVE_JSON, key="") == []


def test_empty_on_error():
    def boom(url, headers):
        raise RuntimeError("network")
    assert search("x", http_get=boom, key="K") == []


def test_scopes_query_to_njit():
    captured = {}

    def cap(url, headers):
        captured["url"] = url
        return BRAVE_JSON
    search("parking rules", http_get=cap, key="K")
    assert "njit.edu" in captured["url"]


# ── web_search (un-scoped, for Scholar discovery) ─────────────────────────────
from v2.integration.njit_search import web_search

SCHOLAR_JSON = json.dumps({"web": {"results": [
    {"url": "https://scholar.google.com/citations?user=ABC&hl=en"},
    {"url": "https://www.njit.edu/profile/x"},
    {"url": "https://example.com/y"},
]}})


def test_web_search_returns_all_urls_unscoped():
    got = web_search("Nirwan Ansari NJIT google scholar", k=5,
                     http_get=lambda url, headers: SCHOLAR_JSON, key="K")
    assert "https://scholar.google.com/citations?user=ABC&hl=en" in got
    assert len(got) == 3                      # not filtered to njit.edu


def test_web_search_not_scoped_to_njit():
    captured = {}
    def cap(url, headers):
        captured["url"] = url
        return SCHOLAR_JSON
    web_search("someone google scholar", http_get=cap, key="K")
    assert "site%3Anjit.edu" not in captured["url"] and "site:njit.edu" not in captured["url"]


def test_web_search_empty_without_key_or_on_error():
    assert web_search("x", http_get=lambda u, h: SCHOLAR_JSON, key="") == []
    def boom(u, h): raise RuntimeError("net")
    assert web_search("x", http_get=boom, key="K") == []
