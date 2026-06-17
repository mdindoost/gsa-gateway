"""Brave Search API client scoped to njit.edu. search(query) -> top njit.edu URLs.

Network is injected (`http_get(url, headers) -> str`) so unit tests need no key. Returns []
on any error (missing key, quota, network) so the live fallback degrades to today's decline.
The API key is read from BRAVE_API_KEY (never committed)."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_UA = "GSA-Gateway-Bot/1.0 (+https://github.com/mdindoost/gsa-gateway)"


def _default_get(url: str, headers: dict) -> str:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.read().decode("utf-8", "replace")


def search(query: str, k: int = 3, http_get=_default_get, key: str | None = None) -> list[str]:
    key = key if key is not None else os.getenv("BRAVE_API_KEY", "")
    if not key:
        return []
    q = f"{query} site:njit.edu"
    url = f"{_ENDPOINT}?{urllib.parse.urlencode({'q': q, 'count': max(k, 5)})}"
    headers = {"X-Subscription-Token": key, "Accept": "application/json", "User-Agent": _UA}
    try:
        results = json.loads(http_get(url, headers)).get("web", {}).get("results", [])
        urls = [r["url"] for r in results if "njit.edu" in (r.get("url") or "")]
        return urls[:k]
    except Exception:
        return []
