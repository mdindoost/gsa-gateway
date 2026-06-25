# YWCC College/Department Crawler — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an office-style "grab-everything" prose crawler for NJIT academic units (pilot: YWCC), typing news/events separately with dates, plus the retriever changes to rank them — and remove the high-stakes heads-up barrier.

**Architecture:** One **generalizable prose engine** (`v2/core/ingestion/college_crawl.py`) driven by a `PROSE_ENTRY_POINTS` registry (adding a college/dept = add entry points). It reuses the proven `eos_crawl`/`web_crawler` DFS spine **as-is** (already host+path scoped via `same_scope`), adding: URL-path page typing (policy/news/event), structured date capture, an in-host people-page skip, and a distinct `created_by='college_crawl'` for reconcile isolation. The people layer (`explore.py`) is unchanged. A separate retriever slice ranks news (recency-decay w/ floor) and events (boost-upcoming-only). Heads-up removal is folded in.

**Tech Stack:** Python 3.11, sqlite3 (+ sqlite-vec), BeautifulSoup4, pytest.

**Design spec:** `docs/superpowers/specs/2026-06-25-ywcc-college-crawler-design.md` (read it; this plan implements it).

## Global Constraints

- **Crawl = mechanical only.** No summarizing/paraphrasing/usage-decisions in the crawler. Page typing is URL-path string matching; date capture is structured-markup extraction only (NO free-text date parsing). Content stored verbatim.
- **NJIT content served verbatim, never withheld.** News floor (0.5) is a HARD invariant, not tunable to 0. No staging/decline/redact.
- **Gated live writes.** Every script that writes the live DB: `hardened_backup(...)` (from `scripts/_area_tag_migrate.py`), dry-run by default, `--commit` to write. Dev-copy first (`cp gsa_gateway.db /tmp/dev.db`). After KB writes: `python v2/scripts/embed_all.py`.
- **Source tags.** Prose rows: `source='crawler'`, `created_by='college_crawl'`. NEVER touch `source='dashboard'` rows or `explore.py` `created_by='crawler'` rows.
- **No commit in core helpers.** `ingest_college(conn, ...)` must NOT call `conn.commit()` — the caller (CLI) owns the transaction (project invariant).
- **Never insert `search_text`** (generated column). Embeddings: `search_document:`/`search_query:` prefixes, L2-normalized (handled by `embed_all.py`).
- **Reviews.** This plan was written from a spec that passed RAG + senior-eng review (findings folded). Show the diff before commit; owner signs off; then restart.
- **Test runner:** `python3 -m pytest <path> -q` from repo root.

---

## File Structure

**Create:**
- `v2/core/ingestion/college_crawl.py` — the generalizable prose engine + `PROSE_ENTRY_POINTS` registry.
- `scripts/crawl_college.py` — gated runner CLI (dry-run default, `--entry <slug>`, `--commit`, `--embed`).
- `v2/tests/test_college_crawl.py` — engine unit tests.
- `v2/tests/test_retriever_recency.py` — retriever recency/typing tests.

**Modify:**
- `v2/core/retrieval/retriever.py` — `_boost_for` → row+now aware; `decay_for` helper; news/event/webpage priors; drop `office_page` from `DEFAULT_EXCLUDE_TYPES`.
- `bot/core/message_handler.py` — remove the two `apply_headsup` call sites.
- `v2/core/database/schema.py` — add an index on `json_extract(metadata,'$.natural_key')` (idempotent in `create_all`).
- `eval/questions.txt` — add YWCC verification questions.

**Delete:**
- `bot/core/headsup.py`, `bot/tests/test_headsup.py`.

---

## Phase A — Prose engine (`college_crawl.py`)

### Task A1: Page-type classifier

**Files:**
- Create: `v2/core/ingestion/college_crawl.py`
- Test: `v2/tests/test_college_crawl.py`

**Interfaces:**
- Produces: `classify_type(url: str) -> str` returning `'news' | 'event' | 'policy'`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_college_crawl.py
from v2.core.ingestion.college_crawl import classify_type


def test_classify_type_by_url_path():
    assert classify_type("https://cs.njit.edu/news/award-2024") == "news"
    assert classify_type("https://cs.njit.edu/announcements/x") == "news"
    assert classify_type("https://computing.njit.edu/events/hackathon") == "event"
    assert classify_type("https://cs.njit.edu/academics/phd") == "policy"
    # segment match, not substring: a 'newsletter' page is not 'news'
    assert classify_type("https://cs.njit.edu/about/newsletter-signup") == "policy"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_college_crawl.py::test_classify_type_by_url_path -v`
Expected: FAIL (module/function not defined)

- [ ] **Step 3: Write minimal implementation**

```python
# v2/core/ingestion/college_crawl.py  (top of new file)
"""Generalizable college/department PROSE crawler (Crawling 2.1).

Pilot: YWCC. Brings data ONLY — fetch → mechanically clean → emit records for the caller to
store in KB. Reuses the eos_crawl / web_crawler DFS spine AS-IS (already host+path scoped via
same_scope). Adds: URL-path page typing, structured date capture, in-host people-page skip,
distinct created_by for reconcile isolation. Makes NO serving/gating/usage decisions.

Spec: docs/superpowers/specs/2026-06-25-ywcc-college-crawler-design.md
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# URL-path segments that mark a page kind (segment match, not substring).
_NEWS_SEGMENTS = ("news", "announcement", "announcements")
_EVENT_SEGMENTS = ("event", "events")


def _segments(url: str) -> list[str]:
    return [s for s in urlparse(url).path.lower().split("/") if s]


def classify_type(url: str) -> str:
    """Mechanically type a page by URL path SEGMENT (not substring): /news,/announcement(s) →
    news; /event(s) → event; else policy. A 'newsletter-signup' page is policy (segment match)."""
    segs = _segments(url)
    if any(s in _NEWS_SEGMENTS for s in segs):
        return "news"
    if any(s in _EVENT_SEGMENTS for s in segs):
        return "event"
    return "policy"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_college_crawl.py::test_classify_type_by_url_path -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/college_crawl.py v2/tests/test_college_crawl.py
git commit -m "feat(college-crawl): URL-path page-type classifier (policy/news/event)"
```

---

### Task A2: In-host people-page skip (segment match on SUPPLEMENTARY_PATHS)

**Files:**
- Modify: `v2/core/ingestion/college_crawl.py`
- Test: `v2/tests/test_college_crawl.py`

**Interfaces:**
- Consumes: `entry_points.SUPPLEMENTARY_PATHS` (tuple of `/segment` people-page paths).
- Produces: `is_people_path(url: str) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
def test_is_people_path_segment_match():
    from v2.core.ingestion.college_crawl import is_people_path
    assert is_people_path("https://cs.njit.edu/faculty") is True
    assert is_people_path("https://cs.njit.edu/faculty/jane-doe") is True
    assert is_people_path("https://computing.njit.edu/people") is True
    assert is_people_path("https://cs.njit.edu/administration") is True
    # real prose that merely starts with the same letters must be KEPT:
    assert is_people_path("https://cs.njit.edu/faculty-handbook") is False
    assert is_people_path("https://cs.njit.edu/academics/phd") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_college_crawl.py::test_is_people_path_segment_match -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

First confirm the source-of-truth tuple exists:
Run: `python3 -c "from v2.core.ingestion import entry_points as e; print(e.SUPPLEMENTARY_PATHS)"`
Expected: a tuple like `('/administration', '/joint-faculty', '/our-people', '/people', '/faculty', '/staff', '/leadership', ...)`. If the exact contents differ, the code below adapts automatically (it reads the live tuple).

```python
# college_crawl.py  (add)
from v2.core.ingestion import entry_points as _ep

# People-page segments = the LAST path segment of each SUPPLEMENTARY_PATH (the in-host people
# listings explore.py owns). Single source of truth — can't drift from the people crawler.
_PEOPLE_SEGMENTS = frozenset(p.strip("/").split("/")[-1].lower() for p in _ep.SUPPLEMENTARY_PATHS)


def is_people_path(url: str) -> bool:
    """True when the URL is a dedicated people/roster page (skip — explore.py owns people).
    Segment match against entry_points.SUPPLEMENTARY_PATHS, so /faculty and /faculty/x match
    but /faculty-handbook (real prose) does not."""
    return any(s in _PEOPLE_SEGMENTS for s in _segments(url))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_college_crawl.py::test_is_people_path_segment_match -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/college_crawl.py v2/tests/test_college_crawl.py
git commit -m "feat(college-crawl): in-host people-page skip from SUPPLEMENTARY_PATHS (segment match)"
```

---

### Task A3: Structured date extraction

**Files:**
- Modify: `v2/core/ingestion/college_crawl.py`
- Test: `v2/tests/test_college_crawl.py`

**Interfaces:**
- Produces: `extract_dates(html: str) -> dict` → keys among `published_at`, `event_start`, `event_end`, `source_updated_at` (ISO-8601 strings), only when present in structured markup.

- [ ] **Step 1: Write the failing test**

```python
def test_extract_dates_structured_only():
    from v2.core.ingestion.college_crawl import extract_dates
    html = '''
      <html><head>
        <meta property="article:published_time" content="2024-03-05T10:00:00Z">
        <script type="application/ld+json">
          {"@type":"Event","startDate":"2026-09-01","endDate":"2026-09-02"}
        </script>
      </head><body>
        <time datetime="2024-03-05">March 5, 2024</time>
        <p>Save the date next Friday</p>
      </body></html>'''
    d = extract_dates(html)
    assert d["published_at"] == "2024-03-05T10:00:00Z"
    assert d["event_start"] == "2026-09-01"
    assert d["event_end"] == "2026-09-02"


def test_extract_dates_absent_when_no_markup():
    from v2.core.ingestion.college_crawl import extract_dates
    # free text only — must NOT be parsed (mechanical-only hard line)
    assert extract_dates("<html><body><p>Event on Sept 1st</p></body></html>") == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_college_crawl.py -k extract_dates -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# college_crawl.py  (add)
import json as _json
from bs4 import BeautifulSoup


def extract_dates(html: str) -> dict:
    """Extract literal dates from STRUCTURED markup only (article:published_time, JSON-LD Event
    start/end, <time datetime>, dateModified). NO free-text parsing. Returns only present keys."""
    out: dict = {}
    soup = BeautifulSoup(html, "html.parser")

    m = soup.find("meta", attrs={"property": "article:published_time"})
    if m and m.get("content"):
        out["published_at"] = m["content"].strip()

    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = _json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict):
                continue
            if str(node.get("@type", "")).lower() == "event":
                if node.get("startDate"):
                    out.setdefault("event_start", str(node["startDate"]).strip())
                if node.get("endDate"):
                    out.setdefault("event_end", str(node["endDate"]).strip())
            if node.get("datePublished"):
                out.setdefault("published_at", str(node["datePublished"]).strip())
            if node.get("dateModified"):
                out.setdefault("source_updated_at", str(node["dateModified"]).strip())

    if "published_at" not in out:
        t = soup.find("time", attrs={"datetime": True})
        if t and t.get("datetime"):
            out["published_at"] = t["datetime"].strip()
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_college_crawl.py -k extract_dates -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/college_crawl.py v2/tests/test_college_crawl.py
git commit -m "feat(college-crawl): structured-only date extraction"
```

---

### Task A4: Prose extraction + entry crawl (adapt the eos spine)

**Files:**
- Modify: `v2/core/ingestion/college_crawl.py`
- Test: `v2/tests/test_college_crawl.py`

**Interfaces:**
- Consumes: `web_crawler.clean_text/select_links/normalize_url`, `eos_crawl.extract_prose`/`ProsePage`/`_main_region`/`_url_rank`.
- Produces:
  - `ProsePage` (reuse `eos_crawl.ProsePage` — `title, content, source_url, images, files`) plus typing/date applied at ingest.
  - `crawl_entry(seed, fetch, max_depth=4, budget=400, delay=0.0, stats=None)` — generator of `(url, html)`.
  - `extract_entry(seed, fetch, max_depth=4, budget=400, delay=0.0) -> EntryResult` where `EntryResult` has `seed, prose: list[ProsePage], skipped: list[str], truncated: bool`.

**Key deltas vs `eos_crawl.crawl_entry` (copy it, then change):**
1. Seeds are **bare-host** (`https://cs.njit.edu/`) → `_in_scope` with `seed_path='/'` passes the whole subdomain; `same_scope` (in `select_links`) already blocks off-host links. **No new host-scoping code.**
2. Add a **`delay`** arg → `time.sleep(delay)` after each fetch (politeness; the eos spine has none).
3. **Skip people pages**: in `extract_entry`, drop any `(url, html)` where `is_people_path(url)` before prose extraction (defense-in-depth; also don't enqueue their links — they're people listings).
4. Default `budget=400` (college sites are larger than offices).

- [ ] **Step 1: Write the failing test** (scoping guard + people-skip + dedup, using an injected fake fetch)

```python
def test_extract_entry_scopes_skips_people_dedups():
    from v2.core.ingestion.college_crawl import extract_entry
    pages = {
        "https://cs.njit.edu/": '<a href="/academics/phd">phd</a> <a href="/faculty">fac</a> '
                                '<a href="https://people.njit.edu/profile/x">x</a>'
                                '<h1>CS Home</h1><div role="main">Welcome to CS.</div>',
        "https://cs.njit.edu/academics/phd": '<h1>PhD</h1><div role="main">PhD in Computer Science requirements.</div>',
        "https://cs.njit.edu/faculty": '<h1>Faculty</h1><div role="main">Prof A. Prof B.</div>',
    }
    seen = []
    def fetch(u):
        seen.append(u)
        return pages.get(u)
    res = extract_entry("https://cs.njit.edu/", fetch, max_depth=3, budget=50)
    urls = {p.source_url for p in res.prose}
    assert "https://cs.njit.edu/academics/phd" in urls       # prose kept
    assert "https://cs.njit.edu/faculty" not in urls          # people page skipped
    assert all("people.njit.edu" not in u for u in seen)      # off-host never fetched
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_college_crawl.py::test_extract_entry_scopes_skips_people_dedups -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# college_crawl.py  (add)
import hashlib
import logging
import time
from dataclasses import dataclass, replace
from urllib.parse import urlparse as _urlparse

from v2.core.ingestion.eos_crawl import (
    ProsePage, extract_prose, _url_rank, _strip_recurring_assets, _canon, _in_scope,
)
from v2.core.ingestion.web_crawler import clean_text, normalize_url, select_links

logger = logging.getLogger(__name__)


@dataclass
class EntryResult:
    seed: str
    prose: list[ProsePage]
    skipped: list[str]
    truncated: bool = False


def crawl_entry(seed, fetch, max_depth=4, budget=400, delay=0.0, stats=None):
    """DFS the seed's subdomain (bare-host seed → whole host). Reuses select_links/same_scope
    (already host-scoped) + the eos seed-path guard. Yields (url, html). Politeness delay added."""
    seed = _canon(normalize_url(seed, seed))
    seed_path = _urlparse(seed).path or "/"
    seen = {seed}
    stack = [(seed, 0)]
    while stack and len(seen) <= budget:
        url, depth = stack.pop()
        html = fetch(url)
        if delay:
            time.sleep(delay)
        if not html:
            continue
        yield url, html
        if depth < max_depth:
            follow, _ = select_links(html, url, seed, relevance_gated=False)
            for u in sorted((_canon(u) for u in follow), reverse=True):
                if u not in seen and _in_scope(seed_path, _urlparse(u).path):
                    seen.add(u)
                    stack.append((u, depth + 1))
    if stats is not None:
        stats["truncated"] = bool(stack)
        if stack:
            logger.warning("crawl_entry: hit budget %d at %s; %d links unfollowed",
                           budget, seed, len(stack))


def extract_entry(seed, fetch, max_depth=4, budget=400, delay=0.0) -> EntryResult:
    """Crawl one prose entry point. Skip people pages (explore.py owns people). Dedup prose by
    content hash (collapse .php/clean-URL aliases). Brings data only; no DB writes."""
    res = EntryResult(seed=_canon(normalize_url(seed, seed)), prose=[], skipped=[])
    by_hash: dict[str, ProsePage] = {}
    order: list[str] = []
    stats: dict = {}
    for url, html in crawl_entry(seed, fetch, max_depth=max_depth, budget=budget,
                                 delay=delay, stats=stats):
        if is_people_path(url):
            continue                                  # people page — explore.py owns it
        page = extract_prose(url, html)
        if page is None:
            res.skipped.append(url)
            continue
        h = hashlib.sha1(page.content.encode("utf-8")).hexdigest()
        if h not in by_hash:
            by_hash[h] = page
            order.append(h)
        elif _url_rank(page.source_url) < _url_rank(by_hash[h].source_url):
            by_hash[h] = page
    res.prose = [by_hash[h] for h in order]
    _strip_recurring_assets(res.prose)
    res.truncated = stats.get("truncated", False)
    return res
```

Note: confirm `_canon`, `_in_scope`, `_strip_recurring_assets`, `_url_rank` are importable from `eos_crawl` (they are module-level in that file). If any is named differently, adjust the import.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_college_crawl.py::test_extract_entry_scopes_skips_people_dedups -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/college_crawl.py v2/tests/test_college_crawl.py
git commit -m "feat(college-crawl): entry DFS (bare-host scope, people-skip, delay, dedup)"
```

---

### Task A5: Ingest (typed + dated, idempotent, no person creation)

**Files:**
- Modify: `v2/core/ingestion/college_crawl.py`
- Test: `v2/tests/test_college_crawl.py`

**Interfaces:**
- Consumes: `EntryResult`, `graph.orgs.ensure_org`, `classify_type`, `extract_dates` (note: dates need the page HTML — see below).
- Produces: `PROSE_SOURCE = "college_crawl"`; `ingest_college(conn, org_slug, org_name, parent_slug, result, html_by_url) -> dict`.

**Note on dates:** `extract_dates` needs raw HTML, but `EntryResult.prose` holds cleaned text. Solution: `extract_entry` also returns a `html_by_url: dict[str,str]` of the raw HTML per kept page (add `res.html_by_url = {}` and populate it in the loop). Update Task A4's `extract_entry` to also stash `res.html_by_url[url] = html` for kept pages, and add `html_by_url: dict = field(default_factory=dict)` to `EntryResult`. (Apply that one-line addition here.)

- [ ] **Step 1: Write the failing test**

```python
import sqlite3, json
from v2.core.database.schema import create_all


def _conn():
    c = sqlite3.connect(":memory:")
    create_all(c)
    # minimal org tree: njit -> ywcc
    from v2.core.graph.orgs import ensure_org
    ensure_org(c, "njit", "NJIT", None, type="university")
    ensure_org(c, "ywcc", "YWCC", "njit", type="college")
    return c


def test_ingest_college_types_dates_idempotent():
    from v2.core.ingestion.college_crawl import (
        ingest_college, EntryResult, PROSE_SOURCE)
    from v2.core.ingestion.eos_crawl import ProsePage
    c = _conn()
    page = ProsePage(title="CS News", content="Prof wins award.",
                     source_url="https://cs.njit.edu/news/award")
    res = EntryResult(seed="https://cs.njit.edu/", prose=[page], skipped=[])
    html_by_url = {"https://cs.njit.edu/news/award":
                   '<meta property="article:published_time" content="2024-03-05T00:00:00Z">'}
    out = ingest_college(c, "computer-science", "Computer Science", "ywcc", res, html_by_url)
    c.commit()
    row = c.execute("SELECT type, created_by, json_extract(metadata,'$.published_at') "
                    "FROM knowledge_items WHERE source_url=?",
                    ("https://cs.njit.edu/news/award",)).fetchone()
    assert row == ("news", PROSE_SOURCE, "2024-03-05T00:00:00Z")
    # idempotent: re-ingest unchanged → no new active row
    ingest_college(c, "computer-science", "Computer Science", "ywcc", res, html_by_url)
    c.commit()
    n = c.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND source_url=?",
                  ("https://cs.njit.edu/news/award",)).fetchone()[0]
    assert n == 1
    # no Person created from prose
    assert c.execute("SELECT COUNT(*) FROM nodes WHERE type='Person'").fetchone()[0] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_college_crawl.py::test_ingest_college_types_dates_idempotent -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# college_crawl.py  (add)
import json
from v2.core.graph.orgs import ensure_org, sync_org_nodes

PROSE_SOURCE = "college_crawl"


def ingest_college(conn, org_slug, org_name, parent_slug, result, html_by_url) -> dict:
    """Write an EntryResult's prose into knowledge_items under one org:
      type = classify_type(url); dates from extract_dates(raw html); created_by=PROSE_SOURCE.
    Content-hash idempotent (unchanged skipped; changed version-bumps old). NO Person creation.
    Does NOT commit (caller owns the transaction)."""
    org_id = ensure_org(conn, org_slug, org_name, parent_slug=parent_slug, type="college")
    sync_org_nodes(conn)
    inserted = updated = unchanged = 0
    for p in result.prose:
        ch = hashlib.sha1(p.content.encode("utf-8")).hexdigest()
        meta = {
            "natural_key": p.source_url,
            "content_hash": ch,
            "images": [list(i) for i in p.images],
            "files": [list(f) for f in p.files],
            "source": PROSE_SOURCE,
        }
        meta.update(extract_dates(html_by_url.get(p.source_url, "")))
        ptype = classify_type(p.source_url)
        row = conn.execute(
            "SELECT id, json_extract(metadata,'$.content_hash') FROM knowledge_items "
            "WHERE is_active=1 AND org_id=? AND json_extract(metadata,'$.natural_key')=? "
            "AND created_by=?", (org_id, p.source_url, PROSE_SOURCE)).fetchone()
        if row and row[1] == ch:
            unchanged += 1
            continue
        if row:
            conn.execute("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
                         "WHERE id=?", (row[0],))
            updated += 1
        else:
            inserted += 1
        conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
            "version,is_active,created_by) VALUES(?,?,?,?,?,?,1,1,?)",
            (org_id, ptype, p.title, p.content, json.dumps(meta), p.source_url, PROSE_SOURCE))
    return {"org_id": org_id, "prose_inserted": inserted, "prose_updated": updated,
            "prose_unchanged": unchanged, "skipped": len(result.skipped)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_college_crawl.py::test_ingest_college_types_dates_idempotent -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/college_crawl.py v2/tests/test_college_crawl.py
git commit -m "feat(college-crawl): typed+dated idempotent ingest (created_by=college_crawl, no person)"
```

---

### Task A6: PROSE_ENTRY_POINTS registry (YWCC members)

**Files:**
- Modify: `v2/core/ingestion/college_crawl.py`
- Test: `v2/tests/test_college_crawl.py`

**Interfaces:**
- Produces: `PROSE_ENTRY_POINTS: list[ProseEntry]` where `ProseEntry(seed, org_slug, org_name, parent_slug)`. Defined HERE (not in `entry_points.ALL_ENTRY_POINTS`) so `explore.py` never crawls them as people hubs.

- [ ] **Step 1: Write the failing test**

```python
def test_prose_entry_points_registry():
    from v2.core.ingestion.college_crawl import PROSE_ENTRY_POINTS, ProseEntry
    slugs = {e.org_slug for e in PROSE_ENTRY_POINTS}
    assert {"ywcc", "computer-science", "informatics", "data-science"} <= slugs
    for e in PROSE_ENTRY_POINTS:
        assert isinstance(e, ProseEntry)
        assert e.seed.startswith("https://") and e.seed.endswith("/")   # bare-host roots
    # NOT registered in the people registry
    from v2.core.ingestion import entry_points as ep
    people_urls = {p.url for p in ep.ALL_ENTRY_POINTS}
    assert all(e.seed not in people_urls for e in PROSE_ENTRY_POINTS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_college_crawl.py::test_prose_entry_points_registry -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# college_crawl.py  (add)
from dataclasses import dataclass as _dc


@_dc(frozen=True)
class ProseEntry:
    seed: str           # bare-host root, e.g. https://cs.njit.edu/
    org_slug: str
    org_name: str
    parent_slug: str


# YWCC pilot. Add a college/dept = add a ProseEntry here (the data registry — no new code).
# Data Science host confirmed at dry-run (Task E2); update the seed if it differs.
PROSE_ENTRY_POINTS: list[ProseEntry] = [
    ProseEntry("https://computing.njit.edu/", "ywcc", "YWCC", "njit"),
    ProseEntry("https://cs.njit.edu/", "computer-science", "Computer Science", "ywcc"),
    ProseEntry("https://informatics.njit.edu/", "informatics", "Informatics", "ywcc"),
    ProseEntry("https://datascience.njit.edu/", "data-science", "Data Science", "ywcc"),
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_college_crawl.py::test_prose_entry_points_registry -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/college_crawl.py v2/tests/test_college_crawl.py
git commit -m "feat(college-crawl): PROSE_ENTRY_POINTS registry (YWCC pilot)"
```

---

## Phase B — Runner + schema

### Task B1: Gated runner CLI `scripts/crawl_college.py`

**Files:**
- Create: `scripts/crawl_college.py`
- Test: `v2/tests/test_college_crawl.py` (test the core `run_entry(conn, entry, fetch)` function, not argparse)

**Interfaces:**
- Consumes: `PROSE_ENTRY_POINTS`, `extract_entry`, `ingest_college`, `scripts/_area_tag_migrate.hardened_backup`.
- Produces: `run_entry(conn, entry: ProseEntry, fetch, max_depth=4, budget=400, delay=0.3) -> dict` (extract + ingest one entry; no commit). CLI wraps it with backup + dry-run/`--commit` + optional `--embed`.

- [ ] **Step 1: Write the failing test**

```python
def test_run_entry_extracts_and_ingests():
    from v2.core.ingestion.college_crawl import ProseEntry
    from scripts.crawl_college import run_entry
    c = _conn()
    pages = {"https://cs.njit.edu/": '<h1>CS</h1><div role="main">Computer Science at NJIT.</div>'}
    out = run_entry(c, ProseEntry("https://cs.njit.edu/", "computer-science",
                                  "Computer Science", "ywcc"),
                    lambda u: pages.get(u), budget=10, delay=0.0)
    c.commit()
    assert out["prose_inserted"] >= 1
    assert c.execute("SELECT COUNT(*) FROM knowledge_items WHERE created_by='college_crawl'"
                     ).fetchone()[0] >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_college_crawl.py::test_run_entry_extracts_and_ingests -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/crawl_college.py
"""Gated runner for the college/department PROSE crawler (Crawling 2.1).

Dry-run by default; --commit writes the live DB (hardened_backup first). --entry <slug> runs one
entry point (independent recrawl); default runs all PROSE_ENTRY_POINTS. People are crawled
separately via scripts/run_explore.py (explore.py owns people) — a full YWCC refresh = both.
"""
from __future__ import annotations
import argparse, sys
from v2.core.database.schema import get_connection
from v2.core.ingestion.college_crawl import (
    PROSE_ENTRY_POINTS, extract_entry, ingest_college)
from v2.core.ingestion.web_crawler import make_fetcher   # robots+UA fetcher


def run_entry(conn, entry, fetch, max_depth=4, budget=400, delay=0.3) -> dict:
    res = extract_entry(entry.seed, fetch, max_depth=max_depth, budget=budget, delay=delay)
    out = ingest_college(conn, entry.org_slug, entry.org_name, entry.parent_slug,
                         res, res.html_by_url)
    out.update(entry=entry.org_slug, truncated=res.truncated)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="gsa_gateway.db")
    ap.add_argument("--entry", help="org_slug of one PROSE_ENTRY_POINTS member; default = all")
    ap.add_argument("--budget", type=int, default=400)
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--embed", action="store_true")
    args = ap.parse_args(argv)

    entries = [e for e in PROSE_ENTRY_POINTS if not args.entry or e.org_slug == args.entry]
    if not entries:
        print(f"no entry matching {args.entry!r}"); sys.exit(2)

    if args.commit:
        from scripts._area_tag_migrate import hardened_backup
        hardened_backup(args.db, label="college-crawl")

    conn = get_connection(args.db)
    fetch = make_fetcher()
    totals = []
    for e in entries:
        out = run_entry(conn, e, fetch, budget=args.budget, delay=args.delay)
        totals.append(out)
        print(out)
    if args.commit:
        conn.commit()
        print("COMMITTED")
        if args.embed:
            import subprocess
            subprocess.run([sys.executable, "v2/scripts/embed_all.py"], check=True)
    else:
        print("DRY RUN — no commit (use --commit to write)")
    return totals


if __name__ == "__main__":
    main()
```

Confirm `make_fetcher` exists in `web_crawler` and returns a `fetch(url)->html|None` (robots+UA). If its name/signature differs, adapt the import + call. (Check: `grep -n "def make_fetcher" v2/core/ingestion/web_crawler.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_college_crawl.py::test_run_entry_extracts_and_ingests -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/crawl_college.py v2/tests/test_college_crawl.py
git commit -m "feat(college-crawl): gated runner CLI (dry-run default, --entry, --commit, --embed)"
```

---

### Task B2: natural_key index (idempotency-query performance)

**Files:**
- Modify: `v2/core/database/schema.py` (in `create_all`, add an idempotent `CREATE INDEX IF NOT EXISTS`)
- Test: `v2/tests/test_college_crawl.py`

- [ ] **Step 1: Write the failing test**

```python
def test_natural_key_index_exists():
    c = _conn()
    idx = c.execute("SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name='idx_ki_natural_key'").fetchone()
    assert idx is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_college_crawl.py::test_natural_key_index_exists -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Find where `create_all` creates indexes in `v2/core/database/schema.py` and add:

```python
conn.execute(
    "CREATE INDEX IF NOT EXISTS idx_ki_natural_key "
    "ON knowledge_items(json_extract(metadata,'$.natural_key'))")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_college_crawl.py::test_natural_key_index_exists -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add v2/core/database/schema.py v2/tests/test_college_crawl.py
git commit -m "perf(schema): index knowledge_items metadata natural_key"
```

---

## Phase C — Retriever slice (recency/typing ranking)

### Task C1: `decay_for(row, now)` + row/now-aware `_boost_for`

**Files:**
- Modify: `v2/core/retrieval/retriever.py`
- Test: `v2/tests/test_retriever_recency.py`

**Interfaces:**
- Produces: a module-level pure `decay_for(row: dict, now: datetime) -> float` and a refactored `V2Retriever._boost_for(self, row: dict, now: datetime) -> float` that calls it. Constants: `NEWS_PRIOR=0.85`, `NEWS_HALFLIFE_DAYS=180`, `NEWS_FLOOR=0.5`, `WEBPAGE_PRIOR=0.8`, `EVENT_BOOST` (existing 1.2).

Behavior (from spec §6.1):
- `news`: `max(NEWS_FLOOR, NEWS_PRIOR * 0.5**(age_days/180))` using `metadata.published_at`; **undated → NEWS_PRIOR, no decay**; future-dated → age 0.
- `event`: `EVENT_BOOST` iff upcoming (`event_end` else `event_start` ≥ start-of-day UTC); else 1.0 (or news-style decay on `published_at` if present); missing/unparseable dates → not upcoming (fail-closed).
- `event_info`: `EVENT_BOOST` (unchanged, unconditional).
- `webpage`: `WEBPAGE_PRIOR`.
- else: `1.0`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_retriever_recency.py
from datetime import datetime, timezone, timedelta
from v2.core.retrieval.retriever import decay_for

NOW = datetime(2026, 6, 25, tzinfo=timezone.utc)

def _row(type_, **meta): return {"type": type_, "metadata": meta}

def test_news_recency_decay_with_floor():
    fresh = decay_for(_row("news", published_at="2026-06-20"), NOW)
    old   = decay_for(_row("news", published_at="2019-01-01"), NOW)
    undated = decay_for(_row("news"), NOW)
    assert 0.80 < fresh <= 0.85
    assert old == 0.5                      # floor, never below
    assert abs(undated - 0.85) < 1e-9      # undated = prior, no decay

def test_event_boost_only_upcoming():
    up   = decay_for(_row("event", event_end="2026-12-01"), NOW)
    past = decay_for(_row("event", event_end="2024-01-01"), NOW)
    dateless = decay_for(_row("event"), NOW)
    assert up == 1.2
    assert past <= 1.0
    assert dateless == 1.0                  # fail-closed: no boost

def test_webpage_and_default_and_eventinfo():
    assert decay_for(_row("webpage"), NOW) == 0.8
    assert decay_for(_row("policy"), NOW) == 1.0
    assert decay_for(_row("event_info"), NOW) == 1.2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_retriever_recency.py -k "decay or upcoming or webpage" -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Add near the top of `retriever.py` (after imports; ensure `from datetime import datetime, timezone, timedelta`):

```python
NEWS_PRIOR = 0.85
NEWS_HALFLIFE_DAYS = 180
NEWS_FLOOR = 0.5          # HARD invariant — never tune to 0 (served, not withheld)
WEBPAGE_PRIOR = 0.8
EVENT_BOOST = 1.2


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.fromisoformat(str(s)[:10])
        except ValueError:
            return None


def _aware(dt, now):
    return dt if dt.tzinfo else dt.replace(tzinfo=now.tzinfo)


def decay_for(row: dict, now: datetime) -> float:
    """Type/recency multiplier (post-RRF). Pure. See spec §6.1."""
    t = row.get("type")
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        import json as _j
        try: meta = _j.loads(meta)
        except ValueError: meta = {}
    if t == "news":
        pub = _parse_iso(meta.get("published_at"))
        if pub is None:
            return NEWS_PRIOR                       # undated: no decay
        age = max(0.0, (now - _aware(pub, now)).total_seconds() / 86400.0)
        return max(NEWS_FLOOR, NEWS_PRIOR * (0.5 ** (age / NEWS_HALFLIFE_DAYS)))
    if t == "event":
        sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = _parse_iso(meta.get("event_end")) or _parse_iso(meta.get("event_start"))
        if end is not None and _aware(end, now) >= sod:
            return EVENT_BOOST                      # upcoming
        return decay_for({"type": "news", "metadata": {"published_at":
                          meta.get("published_at")}}, now) if meta.get("published_at") else 1.0
    if t == "event_info":
        return EVENT_BOOST
    if t == "webpage":
        return WEBPAGE_PRIOR
    return 1.0
```

Then refactor the method `_boost_for` to delegate:

```python
def _boost_for(self, row: dict, now: datetime) -> float:
    return decay_for(row, now)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_retriever_recency.py -k "decay or upcoming or webpage" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/retriever.py v2/tests/test_retriever_recency.py
git commit -m "feat(retriever): decay_for() recency/type multiplier (news/event/webpage)"
```

---

### Task C2: Wire `decay_for` at both boost sites with `now` injection

**Files:**
- Modify: `v2/core/retrieval/retriever.py` (the two old `_boost_for(...type...)` call sites — was ~`:200` rerank and ~`:345` fusion; locate current lines)
- Test: `v2/tests/test_retriever_recency.py`

**Change:** compute `now = datetime.now(timezone.utc)` once in the search method and thread it to both `_boost_for` calls, passing the full `rows[iid]` (not `rows[iid]["type"]`). Old calls look like `self._boost_for(rows[iid]["type"])`; new: `self._boost_for(rows[iid], now)`.

- [ ] **Step 1: Write the failing test** (decay identical with rerank ON vs OFF — the kill-switch divergence guard)

```python
def test_boost_identical_rerank_on_off(monkeypatch):
    # A focused integration test: build a retriever over an in-memory DB with one fresh news
    # item and one policy item on the same topic; assert the news item's final ordering is the
    # same whether rerank_enabled is True or False (decay applied once, identically).
    import v2.core.retrieval.retriever as R
    # (Construct V2Retriever per the existing test fixtures in v2/tests/test_retriever*.py;
    #  reuse that harness. Run the same query with self.rerank_enabled True then False and
    #  assert the news row's rank relative to the policy row is identical.)
    ...
```

Note: model this on the existing retriever test fixture (`grep -n "V2Retriever(" v2/tests/`). If no fixture exists, assert at the unit level that both call sites invoke `decay_for` with the row+now by spying on `decay_for`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_retriever_recency.py::test_boost_identical_rerank_on_off -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Locate both call sites:
Run: `grep -n "_boost_for" v2/core/retrieval/retriever.py`
At the rerank scorer and the fusion loop, change `self._boost_for(rows[iid]["type"])` → `self._boost_for(rows[iid], now)`. Add `now = datetime.now(timezone.utc)` once at the top of the method that fuses/reranks (before both uses), and pass `now` into `_rerank(...)` if the rerank scorer is a nested/closure — thread it through.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_retriever_recency.py -v`
Expected: PASS
Also run the full existing retriever suite to confirm no regression:
Run: `python3 -m pytest v2/tests/ -k retriev -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/retriever.py v2/tests/test_retriever_recency.py
git commit -m "feat(retriever): apply decay_for at both boost sites with shared now"
```

---

### Task C3: Drop `office_page` from DEFAULT_EXCLUDE_TYPES (+ settings note)

**Files:**
- Modify: `v2/core/retrieval/retriever.py:56`
- Test: `v2/tests/test_retriever_recency.py`

**Note:** `webpage` is NO LONGER excluded (it's served at a 0.8 prior via `decay_for`). Remove BOTH `webpage` and `office_page` from `DEFAULT_EXCLUDE_TYPES`; keep `publication`. The live `retriever.exclude_types` SETTING (if set) overrides this default — the migration (Task E2) must check/clear it.

- [ ] **Step 1: Write the failing test**

```python
def test_default_exclude_types():
    from v2.core.retrieval.retriever import DEFAULT_EXCLUDE_TYPES
    assert "publication" in DEFAULT_EXCLUDE_TYPES
    assert "office_page" not in DEFAULT_EXCLUDE_TYPES
    assert "webpage" not in DEFAULT_EXCLUDE_TYPES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_retriever_recency.py::test_default_exclude_types -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
DEFAULT_EXCLUDE_TYPES = frozenset({"publication"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_retriever_recency.py::test_default_exclude_types -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/retriever.py v2/tests/test_retriever_recency.py
git commit -m "feat(retriever): serve webpage (downweighted); drop dead office_page exclusion"
```

---

## Phase D — Heads-up barrier removal

### Task D1: Remove the heads-up wiring + module

**Files:**
- Modify: `bot/core/message_handler.py` (remove the two `apply_headsup(...)` call sites + the import)
- Delete: `bot/core/headsup.py`, `bot/tests/test_headsup.py`
- Test: `bot/tests/test_headsup_removed.py` (new — assert no caution is appended)

- [ ] **Step 1: Write the failing test**

```python
# bot/tests/test_headsup_removed.py
def test_no_headsup_module():
    import importlib
    try:
        importlib.import_module("bot.core.headsup")
        raised = False
    except ModuleNotFoundError:
        raised = True
    assert raised, "bot.core.headsup should be deleted"


def test_message_handler_has_no_headsup_call():
    src = open("bot/core/message_handler.py").read()
    assert "apply_headsup" not in src
    assert "headsup" not in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest bot/tests/test_headsup_removed.py -v`
Expected: FAIL

- [ ] **Step 3: Make the change**

```bash
git rm bot/core/headsup.py bot/tests/test_headsup.py
```
In `bot/core/message_handler.py`: delete `from bot.core.headsup import apply_headsup` and change the two call sites:
- `text = apply_headsup(live.text, topic)` → `text = live.text`
- `response_text = apply_headsup(response_text, clean_text)` → (delete the line; `response_text` already holds the answer)

Run `grep -n "headsup\|apply_headsup" bot/core/message_handler.py` to confirm zero matches, and check no now-unused variable (`topic`) is left dangling (remove its assignment if it was only for headsup).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest bot/tests/test_headsup_removed.py -v`
Expected: PASS
Also: `python3 -m pytest bot/tests/ -q` (confirm no other test imported headsup).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: remove high-stakes heads-up barrier (owner 2026-06-25)"
```

---

## Phase E — Verification & live rollout

### Task E1: Grow the correctness suite

**Files:**
- Modify: `eval/questions.txt`

- [ ] **Step 1: Add YWCC questions** (under a `# ywcc` header)

```
# ywcc
what is the PhD in Computer Science at NJIT
tell me about the Informatics department
what are the admission requirements for YWCC graduate programs
who teaches in the Data Science department
```

- [ ] **Step 2: Commit**

```bash
git add eval/questions.txt
git commit -m "test(eval): add YWCC coverage questions"
```

---

### Task E2: Dev-copy dry-run → live (GATED, owner-supervised)

This task is operational (not unit-tested). Run it on a dev copy first, show the owner, then live.

- [ ] **Step 1: Dev copy + dry-run all YWCC entries**

```bash
cp gsa_gateway.db /tmp/dev.db
python3 scripts/crawl_college.py --db /tmp/dev.db --budget 400 --delay 0.3
```
Confirm Data Science host (datascience.njit.edu) returns pages; if 0, find the real host (`grep`/browser) and fix the `PROSE_ENTRY_POINTS` seed (Task A6), re-run. Inspect per-entry counts + `truncated` flags; raise budgets if truncated.

- [ ] **Step 2: Commit dry-run to dev, inspect typing/dates/scope**

```bash
python3 scripts/crawl_college.py --db /tmp/dev.db --commit
sqlite3 /tmp/dev.db "SELECT o.slug, ki.type, COUNT(*) FROM knowledge_items ki JOIN organizations o ON o.id=ki.org_id WHERE ki.created_by='college_crawl' GROUP BY 1,2 ORDER BY 1,3 DESC;"
sqlite3 /tmp/dev.db "SELECT COUNT(*) FROM knowledge_items WHERE created_by='college_crawl' AND source_url LIKE '%people.njit.edu%';"   # must be 0
sqlite3 /tmp/dev.db "SELECT COUNT(*) FROM knowledge_items WHERE created_by='college_crawl' AND type IN ('news','event') AND json_extract(metadata,'$.published_at') IS NULL AND json_extract(metadata,'$.event_start') IS NULL;"   # dateless count (informational)
```
Verify the 27 `source='dashboard'` rows + all `created_by='crawler'` people rows are untouched:
```bash
sqlite3 /tmp/dev.db "SELECT created_by, COUNT(*) FROM knowledge_items WHERE org_id IN (SELECT id FROM organizations WHERE slug IN ('ywcc','computer-science','informatics','data-science')) GROUP BY 1;"
```

- [ ] **Step 2b: Supersession review query (manual)** — list dashboard rows that may now overlap, for owner to retire by hand:
```bash
sqlite3 /tmp/dev.db "SELECT id, title, source_url FROM knowledge_items WHERE is_active=1 AND created_by='dashboard' AND org_id IN (SELECT id FROM organizations WHERE slug IN ('ywcc','computer-science','informatics','data-science'));"
```

- [ ] **Step 3: Run the retriever exclude-setting check** — if a live `retriever.exclude_types` setting exists, update it:
```bash
sqlite3 /tmp/dev.db "SELECT value FROM settings WHERE key='retriever.exclude_types';"
# if it lists office_page/webpage, update to 'publication' (or delete the row to use the new default)
```

- [ ] **Step 4: Embed + chat-verify on dev** (run the bot against /tmp/dev.db or use `scripts/ask.sh` if it accepts a db) — ask the Task E1 questions; confirm grounded YWCC prose answers, no heads-up line, news/events sane.

- [ ] **Step 5: GO LIVE (owner sign-off gate)** — show the owner the dev results + the full `git diff`. On approval:
```bash
python3 scripts/crawl_college.py --commit --embed     # hardened_backup runs first
bash scripts/restart.sh                                # picks up the headsup code removal
```
Then chat-verify on live. Run the people pass if YWCC people need refresh: `python3 scripts/run_explore.py --commit` (unchanged path).

---

## Self-Review (completed against spec)

- **Spec coverage:** §4.1 scoping (A4, reuse-as-is + guard test), §4.2 people-skip (A2, A4), §4.3 typing (A1, A5), §4.4 dates (A3, A5), §4.5 created_by (A5), §4.7 idempotency/delay/budget/index (A4, A5, B1, B2), §3.1 separate registry (A6), §5 explore unchanged (no task — correct), §6/§6.1 retriever (C1–C3), §7 data model (A5), §8 migration (E2), §9 heads-up (D1), §10 tests (each task), §11 goals (all built items have tasks; deferrals untouched). ✅
- **Placeholder scan:** the only `...` is in C2's integration test, which explicitly defers to the existing retriever test fixture (named, with grep command) — acceptable, not a silent gap.
- **Type consistency:** `ProseEntry`, `EntryResult`(+`html_by_url`), `ingest_college(conn, org_slug, org_name, parent_slug, result, html_by_url)`, `decay_for(row, now)`, `_boost_for(row, now)`, `PROSE_SOURCE='college_crawl'` consistent across tasks. `EntryResult.html_by_url` is introduced in A4's note and consumed in A5/B1 — wired.

## Notes for the executor
- Verify these imports exist before relying on them (grep): `eos_crawl._canon/_in_scope/_url_rank/_strip_recurring_assets/ProsePage/extract_prose`, `web_crawler.make_fetcher`, `entry_points.SUPPLEMENTARY_PATHS`, `scripts._area_tag_migrate.hardened_backup`, `schema.get_connection/create_all`. If any differ, adapt the import (the design is correct; only names may shift).
- This is the PILOT: keep `college_crawl.py` generic; adding NCE/CSLA/HCAD later = append `ProseEntry`s to `PROSE_ENTRY_POINTS` + their orgs, no new code.
