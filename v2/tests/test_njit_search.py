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
