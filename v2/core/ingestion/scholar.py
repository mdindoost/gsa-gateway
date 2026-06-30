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
import random
import re
import time
import urllib.request

from bs4 import BeautifulSoup

# Scholar's right-hand stats table: rows Citations / h-index / i10-index, columns All | Since YYYY.
_LABELS = {"citations": "citations", "h-index": "h_index", "i10-index": "i10_index"}

_UA = "GSA-Gateway/1.0 (+https://gsanjit.com; NJIT Graduate Student Association)"

_SCHOLAR_BASE = "https://scholar.google.com"


def _abs_url(href: str | None) -> str | None:
    """Absolutize a relative Scholar href; None/empty -> None."""
    if not href:
        return None
    return href if href.startswith("http") else _SCHOLAR_BASE + href


def _cluster_id(url: str) -> str | None:
    """The stable per-paper id from a citation link: the `citation_for_view=<USER>:<CLUSTER>`
    token (params/`hl`/`oe` differ between the cited-order and pubdate pages, so the raw URL is
    not a safe merge key — this token is)."""
    m = re.search(r"citation_for_view=([^&]+)", url or "")
    return m.group(1) if m else None


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


def parse_scholar_recent(html: str) -> dict | None:
    """The 'Since YYYY' column of the stats table -> {recent_citations, recent_h_index,
    recent_i10_index, recent_since_year}, or None when the profile has no recent column (a
    third cell). Header is read defensively (by cell count), not by a fixed column index."""
    soup = BeautifulSoup(html or "", "html.parser")
    table = soup.select_one("table#gsc_rsb_st")
    if not table:
        return None
    since_year: int | None = None
    out: dict = {}
    for tr in table.select("tr"):
        th = tr.find_all("th")
        if len(th) >= 3:
            m = re.search(r"\d{4}", th[2].get_text())
            since_year = int(m.group()) if m else None
        td = tr.find_all("td")
        if len(td) >= 3:
            key = _LABELS.get(td[0].get_text(strip=True).lower())
            if not key:
                continue
            val = td[2].get_text(strip=True).replace(",", "")
            if val.lstrip("-").isdigit():
                out["recent_" + key] = int(val)
    if not out:
        return None
    out["recent_since_year"] = since_year
    return out


def parse_cites_per_year(html: str) -> dict:
    """The per-year citation bar chart (#gsc_g_bars) as {"YYYY": int}. {} when absent/blocked.
    Year labels (.gsc_g_t) and bar values (.gsc_g_al) are parallel lists on the page."""
    soup = BeautifulSoup(html or "", "html.parser")
    years = soup.select(".gsc_g_t")
    vals = soup.select(".gsc_g_al")
    # Zip POSITIONALLY (year label <-> bar value are parallel): never pre-filter values, or a single
    # unparseable/comma value would shift every later year onto the wrong count. Strip commas.
    out: dict = {}
    for y_el, v_el in zip(years, vals):
        y = y_el.get_text(strip=True)
        v = v_el.get_text(strip=True).replace(",", "")
        if y and v.isdigit():
            out[y] = int(v)
    return out


def parse_scholar_publications(html: str) -> list[dict]:
    """The publications table (tr.gsc_a_tr) -> [{title, authors, venue, year, cited_by, url}, …].
    cited_by is an int (0 for an uncited/blank cell). [] when absent."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[dict] = []
    for tr in soup.select("tr.gsc_a_tr"):
        a = tr.select_one(".gsc_a_at")
        if not a:
            continue
        grays = tr.select(".gs_gray")
        year_el = tr.select_one(".gsc_a_y span")
        cit_el = tr.select_one(".gsc_a_c a")
        cit = (cit_el.get_text(strip=True) if cit_el else "").replace(",", "").strip()
        out.append({
            "title": a.get_text(strip=True),
            "authors": grays[0].get_text(strip=True) if len(grays) > 0 else "",
            "venue": grays[1].get_text(strip=True) if len(grays) > 1 else "",
            "year": year_el.get_text(strip=True) if year_el else "",
            "cited_by": int(cit) if cit.isdigit() else 0,
            "url": _abs_url(a.get("href")) or "",
        })
    return out


def parse_scholar_coauthors(html: str) -> list[dict]:
    """The co-author list -> [{name, affiliation, url}, …] (all shown, ≤20). [] when absent."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[dict] = []
    for li in soup.select("#gsc_rsb_co .gsc_rsb_aa"):
        a = li.select_one(".gsc_rsb_a_desc a")
        ext = li.select_one(".gsc_rsb_a_ext")
        if not a:
            continue
        out.append({"name": a.get_text(strip=True),
                    "affiliation": ext.get_text(strip=True) if ext else "",
                    "url": _abs_url(a.get("href")) or ""})
    return out


def parse_scholar_profile(html: str) -> dict:
    """Profile scalars -> {photo, homepage, affiliation, public_access}. Each is None when absent;
    public_access is {available, not_available} or None (a 0/0 default would falsely read as a
    real zero, so an absent block -> None)."""
    soup = BeautifulSoup(html or "", "html.parser")
    img = soup.select_one("#gsc_prf_pup-img")
    home = soup.select_one("#gsc_prf_ivh a")
    aff = soup.select_one(".gsc_prf_il")
    pa = None
    # Scope to the .gsc_rsb_m body (the "View all N articles" header link lives OUTSIDE it, so its
    # number can't be mistaken for the available count): first two ints = available, not_available.
    body = soup.select_one("#gsc_rsb_mnd .gsc_rsb_m")
    if body:
        nums = re.findall(r"\d+", body.get_text(" ", strip=True))
        if len(nums) >= 2:
            pa = {"available": int(nums[0]), "not_available": int(nums[1])}
    return {
        "photo": _abs_url(img.get("src")) if img else None,
        "homepage": _abs_url(home.get("href")) if home else None,
        "affiliation": aff.get_text(strip=True) if aff else None,
        "public_access": pa,
    }


def derive_highlights(cited_pubs: list[dict], date_pubs: list[dict],
                      today: datetime.date) -> dict:
    """Merge the citation-ordered and pubdate-ordered pub lists (dedup by cluster id, keeping the
    higher cited_by) and derive the three bounded highlight lists:
      top_cited    – 5 most-cited (all-time)
      newest       – 5 newest (year desc, cited_by tiebreak)
      current_year – ≤10 current-calendar-year papers (most-cited first); [] when none.
    """
    merged: dict = {}
    for p in list(cited_pubs) + list(date_pubs):
        cid = _cluster_id(p.get("url", "")) or (p.get("title"), p.get("year"))
        if cid not in merged or p.get("cited_by", 0) > merged[cid].get("cited_by", 0):
            merged[cid] = p
    pubs = list(merged.values())
    top_cited = sorted(pubs, key=lambda p: p.get("cited_by", 0), reverse=True)[:5]
    newest = sorted([p for p in pubs if p.get("year")],
                    key=lambda p: (p["year"], p.get("cited_by", 0)), reverse=True)[:5]
    cy = str(today.year)
    current = sorted([p for p in pubs if p.get("year") == cy],
                     key=lambda p: p.get("cited_by", 0), reverse=True)[:10]
    return {"top_cited": top_cited, "newest": newest, "current_year": current}


def all_time_peak(citations: int | None, cites_per_year: dict) -> tuple | None:
    """(peak_year, peak_value, is_all_time) for the busiest citation year, or None when inputs are
    missing. The chart is a WINDOW: `hidden = max(0, citations - sum(chart))` citations predate it,
    so only call the peak ALL-TIME when `peak_value > hidden` (the honest-claim guard)."""
    if not citations or not cites_per_year:
        return None
    peak_year, peak_val = max(cites_per_year.items(), key=lambda kv: kv[1])
    hidden = max(0, citations - sum(cites_per_year.values()))
    return (peak_year, peak_val, peak_val > hidden)


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


def _parse_updated(s: str | None) -> datetime.date | None:
    """A stored scholar.updated_at -> date. 'YYYY-MM-DD' exact; legacy 'YYYY-MM' as month-start;
    anything unparseable / missing -> None (treated as never-refreshed = stale)."""
    if not s:
        return None
    parts = str(s).split("-")
    try:
        if len(parts) == 3:
            return datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
        if len(parts) == 2:
            return datetime.date(int(parts[0]), int(parts[1]), 1)
    except (ValueError, TypeError):
        return None
    return None


def select_scholar_targets(conn, *, org_scope: str | None = None,
                           older_than_days: int | None = None,
                           today: datetime.date | None = None) -> list[str]:
    """Person keys eligible for a Scholar refresh: people carrying a Scholar URL, optionally
    restricted to an org subtree (a college includes its departments, via the same
    org_descendants + json_extract(org_id) join the metric ranking uses) and/or only those whose
    scholar.updated_at is older than ``older_than_days``. READ-ONLY (no commit, no node upsert,
    no network). Returns DISTINCT person keys (a person with two in-scope roles appears once)."""
    keys = [k for k, _ in people_with_scholar(conn)]

    if org_scope is not None:
        from v2.core.retrieval.skills import org_descendants
        row = conn.execute(
            "SELECT id FROM organizations WHERE slug=? AND is_active=1", (org_scope,)).fetchone()
        if not row:
            return []
        ids = sorted(org_descendants(conn, row[0]))
        if not ids:
            return []
        ph = ",".join("?" * len(ids))
        in_scope = {r[0] for r in conn.execute(
            f"SELECT DISTINCT p.key FROM edges e JOIN nodes p ON p.id=e.src_id "
            f"JOIN nodes o ON o.id=e.dst_id AND o.is_active=1 "
            f"WHERE e.type='has_role' AND e.is_active=1 AND p.is_active=1 "
            f"AND json_extract(o.attrs,'$.org_id') IN ({ph})", tuple(ids)).fetchall()}
        keys = [k for k in keys if k in in_scope]

    if older_than_days is not None:
        today = today or datetime.date.today()
        kept = []
        for k in keys:
            row = conn.execute(
                "SELECT json_extract(attrs,'$.profiles.scholar.updated_at') "
                "FROM nodes WHERE type='Person' AND key=?", (k,)).fetchone()
            upd = _parse_updated(row[0] if row else None)
            if upd is None or (today - upd).days >= older_than_days:
                kept.append(k)
        keys = kept

    seen: set[str] = set()
    return [k for k in keys if not (k in seen or seen.add(k))]


def scholar_scope_list(conn, mode: str = "have") -> list[dict]:
    """The dashboard scope dropdown: 'All faculty' + each college + each department, each with the
    count of DISTINCT people in that subtree. mode='have' (refresh job) counts people WITH a Scholar
    URL; mode='discover' counts FACULTY WITHOUT one (= how many a discovery run will search).
    Computed in one pass (no per-org subtree walk): expand each counted person's role-orgs up the
    parent chain, then count distinct people per ancestor org. Read-only."""
    from collections import defaultdict
    scholar_keys = {k for k, _ in people_with_scholar(conn)}
    orgs: dict[int, dict] = {}
    parent: dict[int, int | None] = {}
    for oid, slug, name, otype, pid in conn.execute(
            "SELECT id, slug, name, type, parent_id FROM organizations WHERE is_active=1"):
        orgs[oid] = {"slug": slug, "name": name, "type": otype}
        parent[oid] = pid
    discover = mode == "discover"
    cat = "AND e.category='faculty'" if discover else ""   # discovery targets faculty only
    membership: dict[str, set[int]] = defaultdict(set)
    for key, org_id in conn.execute(
            "SELECT p.key, json_extract(o.attrs,'$.org_id') "
            "FROM edges e JOIN nodes p ON p.id=e.src_id "
            "JOIN nodes o ON o.id=e.dst_id AND o.is_active=1 "
            f"WHERE e.type='has_role' AND e.is_active=1 {cat} AND p.is_active=1").fetchall():
        in_set = (key not in scholar_keys) if discover else (key in scholar_keys)
        if in_set and org_id is not None:
            membership[key].add(int(org_id))
    counts: dict[int, set[str]] = defaultdict(set)
    for key, org_ids in membership.items():
        ancestors: set[int] = set()
        for oid in org_ids:
            cur = oid
            while cur is not None and cur in orgs and cur not in ancestors:
                ancestors.add(cur)
                cur = parent.get(cur)
        for a in ancestors:
            counts[a].add(key)
    word = "without Scholar" if discover else "with Scholar"
    total = len(membership) if discover else len(scholar_keys)
    rows = [{"slug": "", "label": f"All faculty ({total} {word})", "type": "all", "eligible": total}]
    colleges = sorted((it for it in orgs.items() if it[1]["type"] == "college"),
                      key=lambda it: it[1]["name"])
    depts = sorted((it for it in orgs.items() if it[1]["type"] == "department"),
                   key=lambda it: it[1]["name"])
    for oid, meta in colleges + depts:
        n = len(counts.get(oid, ()))
        rows.append({"slug": meta["slug"], "label": f'{meta["name"]} ({n} {word})',
                     "type": meta["type"], "eligible": n})
    return rows


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


def _with_param(url: str, param: str) -> str:
    """Append a query param to a Scholar profile URL (which always already has `?user=`)."""
    return url + ("&" if "?" in url else "?") + param


def _today_date(today: str) -> datetime.date:
    """A YYYY-MM-DD (or legacy YYYY-MM) refresh string -> a date (for current-year derivation)."""
    try:
        return datetime.date.fromisoformat(today[:10])
    except ValueError:
        try:
            return datetime.date(int(today[:4]), 1, 1)
        except (ValueError, TypeError):
            return datetime.date.today()


def _existing_history(conn, key: str) -> list[dict]:
    """The person's current append-only scholar.history list (or [])."""
    row = conn.execute("SELECT attrs FROM nodes WHERE type='Person' AND key=?", (key,)).fetchone()
    if not row or not row[0]:
        return []
    try:
        a = json.loads(row[0])
    except (TypeError, ValueError):
        return []
    h = ((a.get("profiles") or {}).get("scholar") or {}).get("history")
    return list(h) if isinstance(h, list) else []


def refresh_scholar(conn, fetch=default_fetch, *, only_key: str | None = None,
                    only_keys: set[str] | None = None,
                    delay: float = 3.0, jitter: tuple[int, int] | None = None,
                    fetch_gap: float = 0.0, block_abort: int = 0,
                    sleep=time.sleep, rand=random.uniform, today: str | None = None) -> dict:
    """Maximal Scholar capture (2026-06-29 design) for every person with a Scholar URL, or just the
    subset in ``only_keys`` (``only_key`` kept for back-compat). Per person, two light fetches —
    the default (citation-ordered) page and the `&sortby=pubdate` (newest) page — fill the complete
    ``attrs.profiles.scholar`` bag: all-time + recent metrics, the per-year chart, the top_cited /
    newest / current_year highlight lists, co-authors, profile scalars, and an APPEND-ONLY history
    snapshot. Research interests still become research areas. ``updated_at`` is a full ISO date.

    ATOMIC: if either fetch fails (or the first page yields no metrics) the person is SKIPPED with
    no write — never a partial write that wipes good prior data. On success the bag is a complete
    snapshot (volatile fields always emitted); ``history`` is the lone append field (existing list
    + today's snapshot, de-duped by date). Does NOT commit — caller owns the txn.

    Anti-block (for a volume run): ``jitter=(lo,hi)`` sleeps a random lo..hi seconds between people
    (overrides ``delay``); ``fetch_gap`` sleeps between a person's 2 fetches; ``block_abort`` (>0)
    stops the run after that many CONSECUTIVE failed/blocked people (a success resets the counter),
    so a Scholar block doesn't burn the whole list. ``sleep``/``rand`` are injectable for tests.
    Returns {people, updated, areas_updated, failed, errors, aborted}."""
    from v2.core.ingestion.people_editor import set_person_profiles, set_person_research_areas
    today = today or datetime.date.today().strftime("%Y-%m-%d")
    tdate = _today_date(today)
    keyset = set(only_keys) if only_keys is not None else ({only_key} if only_key is not None else None)
    targets = [(k, u) for k, u in people_with_scholar(conn) if keyset is None or k in keyset]
    stats = {"people": len(targets), "updated": 0, "areas_updated": 0, "failed": 0,
             "errors": [], "aborted": False}
    consecutive = 0
    for i, (key, url) in enumerate(targets):
        if i:
            sleep(rand(*jitter) if jitter else delay)
        html1, st1 = fetch(url)
        metrics = parse_scholar_metrics(html1) if st1 == "ok" else None
        # require ALL THREE metrics — a partial dict (Scholar drift / a CAPTCHA with a stray number)
        # must fail the person, not crash the job by KeyError-ing on a missing key below.
        if not metrics or not {"citations", "h_index", "i10_index"} <= metrics.keys():
            stats["failed"] += 1
            stats["errors"].append((key, st1 if st1 != "ok" else "no-metrics"))
            consecutive += 1
            if block_abort and consecutive >= block_abort:
                stats["aborted"] = True
                break
            continue
        if fetch_gap:
            sleep(fetch_gap)
        html2, st2 = fetch(_with_param(url, "view_op=list_works&sortby=pubdate"))
        # atomic: a half-fetch (status error OR a 200 CAPTCHA/interstitial with no stats table) must
        # NOT overwrite good newest/current_year with empties. Validate html2 is a real profile page.
        if st2 != "ok" or not parse_scholar_metrics(html2):
            stats["failed"] += 1
            stats["errors"].append((key, st2 if st2 != "ok" else "pubdate-blocked"))
            consecutive += 1
            if block_abort and consecutive >= block_abort:
                stats["aborted"] = True
                break
            continue
        consecutive = 0   # a clean person resets the block counter
        recent = parse_scholar_recent(html1) or {}
        highlights = derive_highlights(parse_scholar_publications(html1),
                                       parse_scholar_publications(html2), tdate)
        # append-only history (read existing, drop same-date, append today's snapshot).
        # Single-writer assumption: refresh jobs are gated and serialized (one dashboard/CLI run at a
        # time), so the read-modify-write needs no lock; two concurrent refreshes of the SAME person
        # could lose-update, which this deployment does not do.
        snap = {"date": today,
                "citations": metrics["citations"], "h_index": metrics["h_index"],
                "i10_index": metrics["i10_index"],
                "recent_citations": recent.get("recent_citations"),
                "recent_h_index": recent.get("recent_h_index"),
                "recent_i10_index": recent.get("recent_i10_index")}
        history = [h for h in _existing_history(conn, key) if h.get("date") != today]
        history.append(snap)
        bag = {
            **metrics,
            "recent_citations": recent.get("recent_citations"),
            "recent_h_index": recent.get("recent_h_index"),
            "recent_i10_index": recent.get("recent_i10_index"),
            "recent_since_year": recent.get("recent_since_year"),
            "cites_per_year": parse_cites_per_year(html1),
            **highlights,
            "coauthors": parse_scholar_coauthors(html1),
            **parse_scholar_profile(html1),
            "updated_at": today,
            "history": history,
        }
        set_person_profiles(conn, person_key=key, profiles={"scholar": bag})
        stats["updated"] += 1
        # S6 (no-manual-ops): capture interests as research areas too, if the person has a home org.
        interests = parse_scholar_interests(html1)
        org_id = _home_org_id(conn, key) if interests else None
        if interests and org_id is not None:
            set_person_research_areas(conn, person_key=key, areas=interests, org_id=org_id)
            stats["areas_updated"] += 1
    return stats
