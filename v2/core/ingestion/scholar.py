"""Google Scholar metrics refresh.

For every person who has a ``attrs.profiles.scholar.url`` (captured by the people crawler or
entered manually), fetch their Scholar profile and update the numeric metrics — citations,
h-index, i10-index — on ``attrs.profiles.scholar``. Idempotent + repeatable (a refresh job),
gated by the caller.

Provider isolation (the live-fallback / njit_search pattern): the HTTP ``fetch`` is INJECTED.
``parse_scholar_metrics`` is provider-agnostic — it parses the Scholar profile HTML however it
was obtained. ``default_fetch`` is a best-effort urllib reader; Google Scholar disallows scraping
in robots.txt and actively rate-limits/blocks bots, so for anything beyond a tiny, polite refresh
swap in a sanctioned provider (e.g. SerpAPI's Scholar endpoint) — that's a one-function change.
"""
from __future__ import annotations

import datetime
import json
import time
import urllib.request

from bs4 import BeautifulSoup

# Scholar's right-hand stats table: rows Citations / h-index / i10-index, columns All | Since YYYY.
_LABELS = {"citations": "citations", "h-index": "h_index", "i10-index": "i10_index"}

_UA = "GSA-Gateway/1.0 (+https://gsanjit.com; NJIT Graduate Student Association)"


def parse_scholar_metrics(html: str) -> dict | None:
    """{citations, h_index, i10_index} (the 'All' column) from a Scholar profile page, or
    None if the stats table isn't present (e.g. a captcha/blocked response)."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: dict = {}
    for tr in soup.select("table#gsc_rsb_st tr"):
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue
        key = _LABELS.get(cells[0].get_text(strip=True).lower())
        if not key:
            continue
        val = cells[1].get_text(strip=True).replace(",", "")
        if val.lstrip("-").isdigit():
            out[key] = int(val)
    return out or None


def parse_scholar_interests(html: str) -> list[str]:
    """The self-asserted research-interest tags (#gsc_prf_int) from a Scholar profile page,
    trimmed and de-duplicated (order-preserving). [] when none / blocked."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.select("#gsc_prf_int a"):
        t = a.get_text(strip=True)
        if t and t.casefold() not in seen:
            seen.add(t.casefold())
            out.append(t)
    return out


def people_with_scholar(conn) -> list[tuple[str, str]]:
    """(person_key, scholar_url) for every active person carrying a Scholar profile URL."""
    out: list[tuple[str, str]] = []
    for key, raw in conn.execute(
            "SELECT key, attrs FROM nodes WHERE type='Person' AND is_active=1 "
            "AND attrs LIKE '%scholar%'").fetchall():
        try:
            a = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            continue
        url = ((a.get("profiles") or {}).get("scholar") or {}).get("url")
        if url:
            out.append((key, url))
    return out


def default_fetch(url: str, timeout: int = 20) -> tuple[str, str]:
    """Best-effort (html, status) reader with the project UA. status is 'ok' or 'error:<reason>'.
    NOTE: Scholar blocks bots — expect this to fail at volume; swap a sanctioned provider in."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace"), "ok"
    except Exception as exc:  # noqa: BLE001 - a failed fetch is just a skip, never fatal
        return "", f"error:{type(exc).__name__}"


def _home_org_id(conn, person_key: str) -> int | None:
    """The person's faculty-home org_id (where their Scholar research areas should be filed so
    org-scoped 'who works on X' finds them): prefer a faculty role, then the primary role, then any."""
    rows = conn.execute(
        "SELECT e.category, json_extract(e.attrs,'$.is_primary'), json_extract(o.attrs,'$.org_id') "
        "FROM edges e JOIN nodes p ON p.id=e.src_id JOIN nodes o ON o.id=e.dst_id "
        "WHERE p.key=? AND e.type='has_role' AND e.is_active=1", (person_key,)).fetchall()
    for want in (lambda c, pr: c == "faculty", lambda c, pr: bool(pr), lambda c, pr: True):
        for cat, prim, oid in rows:
            if oid is not None and want(cat, prim):
                return oid
    return None


def refresh_scholar(conn, fetch=default_fetch, *, only_key: str | None = None,
                    delay: float = 3.0, today: str | None = None) -> dict:
    """Fetch + update Scholar metrics AND research interests for every person with a Scholar URL
    (or just ``only_key``). Metrics deep-merge via set_person_profiles (keeps the url); interests
    become research areas via set_person_research_areas (source='scholar', filed under the faculty
    home org). Does NOT commit — caller owns the txn. Returns {people, updated, areas_updated, failed, errors}."""
    from v2.core.ingestion.people_editor import set_person_profiles, set_person_research_areas
    today = today or datetime.date.today().strftime("%Y-%m")
    targets = [(k, u) for k, u in people_with_scholar(conn) if only_key in (None, k)]
    stats = {"people": len(targets), "updated": 0, "areas_updated": 0, "failed": 0, "errors": []}
    for i, (key, url) in enumerate(targets):
        if i and delay:
            time.sleep(delay)
        html, status = fetch(url)
        metrics = parse_scholar_metrics(html) if status == "ok" else None
        if not metrics:
            stats["failed"] += 1
            stats["errors"].append((key, status if status != "ok" else "no-metrics"))
            continue
        metrics["updated_at"] = today
        set_person_profiles(conn, person_key=key, profiles={"scholar": metrics})
        stats["updated"] += 1
        # S6 (no-manual-ops): capture interests as research areas too, if the person has a home org.
        interests = parse_scholar_interests(html)
        org_id = _home_org_id(conn, key) if interests else None
        if interests and org_id is not None:
            set_person_research_areas(conn, person_key=key, areas=interests, org_id=org_id)
            stats["areas_updated"] += 1
    return stats
