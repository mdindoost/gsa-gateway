# Prose Harvest — Plan A: Harvest Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harvest one NJIT office hub's prose sub-tree into the KB as `type='office_page'` rows, via a DB-backed entry-point registry, an `aspect="office"` crawl policy, a deterministic quality gate, and a hybrid (chunk-embed + grounded-extract-for-high-stakes) gated CLI.

**Architecture:** Reuse the existing `web_crawler` BFS (`crawl_site`) and `make_fetcher` (SSRF/robots/polite), adding (1) a relevance-gate bypass for office sweeps and a status-surfacing fetch, (2) a new `crawl_entry_points` registry table + thin accessor, (3) a pure pre-ingest quality gate, (4) an office-ingest module reusing `upsert_doc_items(doc_type='office_page')` for generic prose and the existing `is_active=0/stakes='high'` staging path for high-stakes procedural pages. A gated CLI ties it together (dry-run default, `hardened_backup`, `--commit`).

**Tech Stack:** Python 3.11, SQLite (STRICT tables), `bs4`, the project's tiktoken/section chunker, Ollama (`grounded_extract` for the extract leg). Tests: pytest.

## Global Constraints

- **Source tagging:** all office prose is `source='crawler'` / `created_by='crawler'` (reconcile/recrawl own it; never clobbers `'dashboard'`). — spec §7
- **Never insert `search_text`** — it is a generated column (`title || ' ' || content`). `upsert_doc_items` already respects this. — spec §7
- **New KB type:** office prose uses `knowledge_items.type = 'office_page'` (the column the retriever filters on; passed as `doc_type='office_page'`). — spec §4.3
- **Gated live writes:** any script writing the live DB takes a `hardened_backup(...)` (from `scripts/_area_tag_migrate.py`), defaults to **dry-run**, requires `--commit`; dev-copy (`/tmp/dev.db`) first. — spec §7
- **Graph/DB-write transactions:** core helpers do NOT commit — the CLI wrapper owns the transaction + backup. — repo invariant
- **Phase-1 registry is `aspect="office"` rows ONLY.** Do NOT migrate the hardcoded `aspect="people"` `ALL_ENTRY_POINTS` — that breaks NCE/HCAD/MTSM. — spec §3 [SE4]
- **Honest-partial / extractive:** high-stakes procedural pages (OPT/CPT/I-20, deadlines, billing/$-amounts) go through the **extract-only** leg (verbatim spans), never the generative chunk leg. — spec §4.3 [RA4]
- **Office crawl is NOT relevance-gated** (the people vocabulary would harvest ~1–2 pages of an office tree); follow all same-scope HTML links, bounded by `scope_prefix` + per-entry `budget`/`depth`. — spec §4.2 [SE1]
- **HARD GATE:** show diffs for sign-off before commit; this plan is built TDD.

---

## File structure

- **Create** `v2/core/ingestion/entry_point_store.py` — registry accessor (`list_active`, `upsert_candidate`, `mark_crawled`, `activate`, `add_seed`).
- **Modify** `v2/core/database/schema.py` — add the STRICT `crawl_entry_points` table to `create_all`.
- **Modify** `v2/core/ingestion/web_crawler.py` — `select_links(..., relevance_gated=True)`; `crawl_site(..., relevance_gated=True)`; new `fetch_with_status()` (status-surfacing) + keep `make_fetcher` backward-compatible.
- **Create** `v2/core/ingestion/office_quality.py` — pure pre-ingest quality gate (`is_low_quality`, `dedup_boilerplate`).
- **Create** `v2/core/ingestion/office_ingest.py` — `is_high_stakes(url, text)`, `ingest_office_page(...)` (chunk-embed generic / stage high-stakes).
- **Create** `scripts/harvest_office.py` — gated CLI: crawl one entry point → quality gate → ingest.
- **Create** tests: `v2/tests/test_entry_point_store.py`, `v2/tests/test_office_link_policy.py`, `v2/tests/test_office_quality.py`, `v2/tests/test_office_ingest.py`.

---

### Task 1: Entry-point registry table + accessor

**Files:**
- Modify: `v2/core/database/schema.py` (add table in `create_all`)
- Create: `v2/core/ingestion/entry_point_store.py`
- Test: `v2/tests/test_entry_point_store.py`

**Interfaces:**
- Produces:
  - `add_seed(conn, *, url, scope_prefix, org_slug, parent_slug, org_type, crawl_interval_days=None) -> int`
  - `upsert_candidate(conn, *, url, discovered_from_url) -> int` (status `'candidate'`, idempotent on `url`)
  - `activate(conn, ep_id) -> None`
  - `list_active(conn, aspect='office') -> list[sqlite3.Row]`
  - `mark_crawled(conn, ep_id) -> None` (sets `last_crawled_at`)

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_entry_point_store.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.ingestion import entry_point_store as eps


def test_seed_is_active_candidate_is_not_until_activated(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    sid = eps.add_seed(conn, url="https://www.njit.edu/parking/", scope_prefix="/parking/",
                       org_slug="eos", parent_slug="njit", org_type="office")
    cid = eps.upsert_candidate(conn, url="https://www.njit.edu/mailroom/",
                               discovered_from_url="https://www.njit.edu/parking/")
    active = [r["url"] for r in eps.list_active(conn, aspect="office")]
    assert "https://www.njit.edu/parking/" in active
    assert "https://www.njit.edu/mailroom/" not in active     # candidate, not active
    eps.activate(conn, cid)
    active2 = [r["url"] for r in eps.list_active(conn, aspect="office")]
    assert "https://www.njit.edu/mailroom/" in active2


def test_upsert_candidate_is_idempotent_on_url(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    a = eps.upsert_candidate(conn, url="https://www.njit.edu/x/", discovered_from_url="h")
    b = eps.upsert_candidate(conn, url="https://www.njit.edu/x/", discovered_from_url="h2")
    assert a == b
    assert len(list(eps.list_active(conn, aspect="office"))) == 0    # still candidate
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_entry_point_store.py -q`
Expected: FAIL — `ModuleNotFoundError: ...entry_point_store` (and no `crawl_entry_points` table).

- [ ] **Step 3: Add the table to `schema.py`**

In `v2/core/database/schema.py`, inside `create_all`'s DDL block (alongside the other `CREATE TABLE IF NOT EXISTS`), add:

```sql
CREATE TABLE IF NOT EXISTS crawl_entry_points (
    id              INTEGER PRIMARY KEY,
    url             TEXT    NOT NULL UNIQUE,
    scope_prefix    TEXT    NOT NULL DEFAULT '',
    aspect          TEXT    NOT NULL DEFAULT 'office',
    org_slug        TEXT,
    parent_slug     TEXT,
    org_type        TEXT    NOT NULL DEFAULT 'office',
    status          TEXT    NOT NULL DEFAULT 'candidate',
    source          TEXT    NOT NULL DEFAULT 'discovered',
    discovered_from_url TEXT,
    last_crawled_at TEXT,
    crawl_interval_days INTEGER,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
) STRICT;
```

- [ ] **Step 4: Write the accessor**

```python
# v2/core/ingestion/entry_point_store.py
"""DB-backed entry-point registry (aspect='office' rows only — spec §3 [SE4]).
One writer per fact; the crawler reads (list_active) and writes (upsert_candidate,
mark_crawled). add_seed creates an already-active office entry point; upsert_candidate
records a discovered hub awaiting gated activation."""
from __future__ import annotations

import sqlite3


def add_seed(conn: sqlite3.Connection, *, url: str, scope_prefix: str, org_slug: str,
             parent_slug: str, org_type: str = "office",
             crawl_interval_days: int | None = None) -> int:
    row = conn.execute("SELECT id FROM crawl_entry_points WHERE url=?", (url,)).fetchone()
    if row:
        conn.execute("UPDATE crawl_entry_points SET status='active', source='seed', "
                     "scope_prefix=?, org_slug=?, parent_slug=?, org_type=?, "
                     "crawl_interval_days=? WHERE id=?",
                     (scope_prefix, org_slug, parent_slug, org_type, crawl_interval_days, row[0]))
        return row[0]
    cur = conn.execute(
        "INSERT INTO crawl_entry_points(url,scope_prefix,aspect,org_slug,parent_slug,"
        "org_type,status,source,crawl_interval_days) "
        "VALUES(?,?,'office',?,?,?,'active','seed',?)",
        (url, scope_prefix, org_slug, parent_slug, org_type, crawl_interval_days))
    return cur.lastrowid


def upsert_candidate(conn: sqlite3.Connection, *, url: str, discovered_from_url: str) -> int:
    row = conn.execute("SELECT id FROM crawl_entry_points WHERE url=?", (url,)).fetchone()
    if row:
        return row[0]                          # idempotent: never downgrade an existing row
    cur = conn.execute(
        "INSERT INTO crawl_entry_points(url,aspect,status,source,discovered_from_url) "
        "VALUES(?,'office','candidate','discovered',?)", (url, discovered_from_url))
    return cur.lastrowid


def activate(conn: sqlite3.Connection, ep_id: int) -> None:
    conn.execute("UPDATE crawl_entry_points SET status='active' WHERE id=?", (ep_id,))


def list_active(conn: sqlite3.Connection, aspect: str = "office") -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM crawl_entry_points WHERE status='active' AND aspect=? ORDER BY id",
        (aspect,)).fetchall()


def mark_crawled(conn: sqlite3.Connection, ep_id: int) -> None:
    conn.execute("UPDATE crawl_entry_points SET last_crawled_at=datetime('now') WHERE id=?",
                 (ep_id,))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest v2/tests/test_entry_point_store.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add v2/core/database/schema.py v2/core/ingestion/entry_point_store.py v2/tests/test_entry_point_store.py
git commit -m "feat(prose-harvest): crawl_entry_points registry table + accessor (office rows)"
```

---

### Task 2: Status-surfacing fetch (SE3) — backward compatible

**Files:**
- Modify: `v2/core/ingestion/web_crawler.py` (add `fetch_with_status`; keep `make_fetcher`)
- Test: `v2/tests/test_office_link_policy.py` (shared test module; this task adds one test)

**Interfaces:**
- Produces: `fetch_with_status(timeout=TIMEOUT) -> Callable[[str], tuple[str|None, int|None]]` — returns `(html, status)`; `status` is the HTTP code (e.g. 200, 404, 410) or `None` on a transport error (timeout/DNS/SSRF-block) so a transient failure is distinguishable from a real 404 (the [SE3] retire guard, used in Plan C).
- `make_fetcher` stays `Callable[[str], str|None]` (the one caller, `scripts/ingest_faculty.py:104`, is unchanged) — it becomes a thin wrapper over `fetch_with_status`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_office_link_policy.py  (new file; more tests added in A3)
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.ingestion import web_crawler as wc


def test_make_fetcher_still_returns_html_only():
    # Backward-compat: the existing signature (html|None) is preserved.
    f = wc.make_fetcher()
    assert callable(f)


def test_fetch_with_status_exists_and_is_callable():
    f = wc.fetch_with_status()
    assert callable(f)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_office_link_policy.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'fetch_with_status'`.

- [ ] **Step 3: Refactor `make_fetcher` into `fetch_with_status` + thin wrapper**

In `web_crawler.py`, replace the body of `make_fetcher` so the network logic lives in `fetch_with_status` and `make_fetcher` wraps it (drop the status). Keep the SSRF guard, robots check, UA, HTML-only, size cap exactly as today; only the return shape changes in the new function:

```python
def fetch_with_status(timeout: int = TIMEOUT):
    """Like make_fetcher but returns (html|None, status|None). status is the HTTP code
    (200/404/410/…) or None on a transport error (timeout/DNS/SSRF-block), so a transient
    failure is distinguishable from a real 404 — the [SE3] retire guard (Plan C)."""
    opener = urllib.request.build_opener(_SafeRedirect())
    robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _allowed(url: str) -> bool:
        host = urlparse(url).scheme + "://" + urlparse(url).netloc
        rp = robots_cache.get(host, "miss")
        if rp == "miss":
            rp = urllib.robotparser.RobotFileParser()
            try:
                rp.set_url(host + "/robots.txt"); rp.read()
            except Exception:  # noqa: BLE001
                rp = None
            robots_cache[host] = rp
        return rp is None or rp.can_fetch(UA, url)

    def fetch(url: str):
        if not is_safe_url(url):
            return None, None
        if not _allowed(url):
            return None, None
        try:
            req = Request(url, headers={"User-Agent": UA})
            with opener.open(req, timeout=timeout) as r:
                ctype = r.headers.get("Content-Type", "")
                status = getattr(r, "status", None) or r.getcode()
                if "html" not in ctype.lower():
                    return None, status
                return r.read(MAX_FETCH_BYTES).decode("utf-8", "ignore"), status
        except urllib.error.HTTPError as e:       # 404/410/5xx carry a real status
            return None, e.code
        except Exception:  # noqa: BLE001 - transport error: status unknown
            return None, None

    return fetch


def make_fetcher(timeout: int = TIMEOUT):
    """A real fetcher returning html|None (backward-compatible). Robots/SSRF/UA/HTML-only."""
    inner = fetch_with_status(timeout)
    return lambda url: inner(url)[0]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest v2/tests/test_office_link_policy.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Regression-check the existing caller imports**

Run: `python3 -c "import scripts.ingest_faculty"` (from repo root)
Expected: no error (the `from web_crawler import crawl_site, make_fetcher` still resolves).

- [ ] **Step 6: Commit**

```bash
git add v2/core/ingestion/web_crawler.py v2/tests/test_office_link_policy.py
git commit -m "feat(prose-harvest): status-surfacing fetch_with_status; make_fetcher backward-compatible [SE3]"
```

---

### Task 3: `aspect="office"` link policy (follow all same-scope links)

**Files:**
- Modify: `v2/core/ingestion/web_crawler.py` (`select_links` + `crawl_site` gain `relevance_gated`)
- Test: `v2/tests/test_office_link_policy.py` (add tests)

**Interfaces:**
- Consumes: existing `select_links(html, current_url, seed_url)` and `crawl_site(seed_url, fetch, max_depth, budget, delay)`.
- Produces:
  - `select_links(html, current_url, seed_url, relevance_gated=True)` — when `False`, return **all** same-scope HTML links (skip `is_relevant`); assets still dropped.
  - `crawl_site(seed_url, fetch, max_depth=DEFAULT_DEPTH, budget=DEFAULT_BUDGET, delay=0.0, relevance_gated=True)` — passes the flag through; office callers pass `relevance_gated=False` with a larger `budget`/`max_depth`.

- [ ] **Step 1: Write the failing test**

```python
# add to v2/tests/test_office_link_policy.py
SEED = "https://www.njit.edu/parking/"
HTML = """
<a href="/parking/visitor-parking">Visitor Parking</a>
<a href="/parking/permits">Permits and fees</a>
<a href="/parking/style.css">css</a>
<a href="https://external.example.com/parking/x">external</a>
"""

def test_office_policy_follows_all_same_scope_links_not_just_relevant():
    follow, files = wc.select_links(HTML, SEED, SEED, relevance_gated=False)
    assert "https://www.njit.edu/parking/visitor-parking" in follow
    assert "https://www.njit.edu/parking/permits" in follow      # NOT in people-relevance vocab
    assert not any(u.endswith(".css") for u in follow)           # assets still dropped
    assert not any("external.example.com" in u for u in follow)  # off-scope dropped

def test_people_policy_unchanged_still_relevance_gated():
    follow, _ = wc.select_links(HTML, SEED, SEED, relevance_gated=True)
    # "permits"/"visitor-parking" are not in the people RELEVANCE vocab → not followed.
    assert follow == set() or all("permit" not in u and "visitor" not in u for u in follow)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_office_link_policy.py -q`
Expected: FAIL — `select_links() got an unexpected keyword argument 'relevance_gated'`.

- [ ] **Step 3: Add the flag to `select_links` and `crawl_site`**

In `web_crawler.py`, change the `select_links` signature and the `is_relevant` gates:

```python
def select_links(html: str, current_url: str, seed_url: str, relevance_gated: bool = True):
    """From one page: (HTML links to follow, recorded non-HTML files). Pure. When
    relevance_gated=False (aspect='office'), follow ALL same-scope HTML links (the people
    vocabulary would harvest ~1–2 pages of an office tree) — assets still dropped. [SE1]"""
    follow: set[str] = set()
    files: set[str] = set()
    try:
        soup = BeautifulSoup(html, "html.parser")
    except ParserRejectedMarkup:
        logger.warning("select_links: malformed markup at %s", current_url, exc_info=True)
        return follow, files
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("mailto:", "javascript:", "#", "tel:")):
            continue
        url = normalize_url(href, current_url)
        if not urlparse(url).scheme.startswith("http"):
            continue
        if not same_scope(seed_url, url):
            continue
        anchor = a.get_text(" ", strip=True)
        if is_non_html(url):
            if relevance_gated and is_relevant(anchor, url):
                files.add(url)
            elif not relevance_gated:
                pass                            # office: skip non-HTML files (prose only)
            continue
        if (not relevance_gated) or is_relevant(anchor, url):
            follow.add(url)
    return follow, files
```

Then thread the flag through `crawl_site`:

```python
def crawl_site(seed_url, fetch, max_depth=DEFAULT_DEPTH, budget=DEFAULT_BUDGET,
               delay=0.0, relevance_gated=True):
    ...
        if depth < max_depth:
            follow, nf = select_links(html, url, seed, relevance_gated=relevance_gated)
            ...
```

(Only the `select_links` call inside `crawl_site` changes — pass `relevance_gated=relevance_gated`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest v2/tests/test_office_link_policy.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Regression — existing crawler tests still green**

Run: `python3 -m pytest v2/tests/ -q -k "web_crawler or crawl or ingest_faculty"`
Expected: no new failures vs baseline (people path unchanged; `relevance_gated` defaults True).

- [ ] **Step 6: Commit**

```bash
git add v2/core/ingestion/web_crawler.py v2/tests/test_office_link_policy.py
git commit -m "feat(prose-harvest): aspect=office link policy (follow all same-scope links) [SE1]"
```

---

### Task 4: Pre-ingest quality gate (RA5)

**Files:**
- Create: `v2/core/ingestion/office_quality.py`
- Test: `v2/tests/test_office_quality.py`

**Interfaces:**
- Produces:
  - `is_low_quality(text, *, min_chars=200, min_words=40, max_link_density=0.5) -> bool` — True for nav/boilerplate/near-empty chunks (drop them).
  - `dedup_boilerplate(pages: list[tuple[str, str]]) -> list[tuple[str, str]]` — given `(url, text)`, remove lines that repeat across ≥ half the pages (shared nav/footer), return cleaned pages.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_office_quality.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.ingestion.office_quality import is_low_quality, dedup_boilerplate


def test_low_quality_drops_short_and_navlike():
    assert is_low_quality("Home About Contact Apply Visit")           # too few words
    assert is_low_quality("")                                          # empty
    assert not is_low_quality(
        "Visitor parking is available in the Lock Street Deck. " * 8)  # real prose


def test_dedup_removes_shared_nav_lines():
    nav = "Home\nDirectory\nApply Now"
    pages = [("u1", nav + "\nParking permits cost $X per year."),
             ("u2", nav + "\nThe mailroom is in Campus Center 220.")]
    out = dict(dedup_boilerplate(pages))
    assert "Apply Now" not in out["u1"]            # repeated nav line removed
    assert "Parking permits cost $X per year." in out["u1"]   # unique content kept
    assert "The mailroom is in Campus Center 220." in out["u2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_office_quality.py -q`
Expected: FAIL — `ModuleNotFoundError: ...office_quality`.

- [ ] **Step 3: Implement the gate**

```python
# v2/core/ingestion/office_quality.py
"""Deterministic pre-ingest quality gate for office prose (no LLM). Drops nav/boilerplate
and near-empty chunks, and strips lines repeated across an office sub-tree (shared nav/footer).
spec §4.3 [RA5]."""
from __future__ import annotations

import re
from collections import Counter

_WORD = re.compile(r"\w+")


def is_low_quality(text: str, *, min_chars: int = 200, min_words: int = 40,
                   max_link_density: float = 0.5) -> bool:
    t = (text or "").strip()
    if len(t) < min_chars:
        return True
    words = _WORD.findall(t)
    if len(words) < min_words:
        return True
    # link/menu density proxy: a high ratio of short capitalised nav tokens
    short_caps = sum(1 for w in words if w[:1].isupper() and len(w) <= 12)
    if words and short_caps / len(words) > max_link_density and len(words) < 120:
        return True
    return False


def dedup_boilerplate(pages: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Remove lines that repeat across >= half the pages (shared nav/footer)."""
    if len(pages) < 2:
        return pages
    counts: Counter[str] = Counter()
    for _url, text in pages:
        for line in {ln.strip() for ln in text.splitlines() if ln.strip()}:
            counts[line] += 1
    threshold = max(2, (len(pages) + 1) // 2)
    boiler = {ln for ln, n in counts.items() if n >= threshold}
    out: list[tuple[str, str]] = []
    for url, text in pages:
        kept = "\n".join(ln for ln in text.splitlines() if ln.strip() not in boiler)
        out.append((url, kept.strip()))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest v2/tests/test_office_quality.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/office_quality.py v2/tests/test_office_quality.py
git commit -m "feat(prose-harvest): deterministic pre-ingest quality gate [RA5]"
```

---

### Task 5: Hybrid office ingest — chunk-embed generic / stage high-stakes

**Files:**
- Create: `v2/core/ingestion/office_ingest.py`
- Test: `v2/tests/test_office_ingest.py`

**Interfaces:**
- Consumes: `upsert_doc_items(conn, *, org_id, slug, title, text, source_url, doc_type, source, is_active, stakes)` (from `gsa_docs.py`); `ensure_org` (from `graph/orgs.py`).
- Produces:
  - `is_high_stakes(url, text) -> bool` — True for OPT/CPT/I-20, deadline, billing/$-amount pages (extract-only leg). [RA4]
  - `ingest_office_page(conn, *, org_id, url, title, text) -> tuple[int, str]` — returns `(chunk_count, leg)` where `leg ∈ {'chunk','staged'}`. Generic prose → `upsert_doc_items(doc_type='office_page', source='crawler', is_active=1)`. High-stakes → staged via `is_active=0, stakes='high'` (the existing human-sign-off path) so it never auto-goes-live ungrounded.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_office_ingest.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion.office_ingest import ingest_office_page, is_high_stakes


def test_high_stakes_classifier():
    assert is_high_stakes("https://www.njit.edu/global/opt-cpt", "Apply for OPT ...")
    assert is_high_stakes("https://www.njit.edu/bursar/fees", "Tuition is $X due by ...")
    assert not is_high_stakes("https://www.njit.edu/parking/visitor-parking",
                              "Visitor parking is in the Lock Street Deck.")


def test_generic_page_goes_live_as_office_page(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        oid = ensure_org(conn, slug="eos", name="EOS", parent_slug=None, type="office")
        n, leg = ingest_office_page(conn, org_id=oid,
                                    url="https://www.njit.edu/parking/visitor-parking",
                                    title="Visitor Parking",
                                    text="Visitor parking is available in the Lock Street Deck. " * 8)
    assert leg == "chunk" and n >= 1
    row = conn.execute("SELECT type,is_active,created_by FROM knowledge_items LIMIT 1").fetchone()
    assert row["type"] == "office_page" and row["is_active"] == 1 and row["created_by"] == "crawler"


def test_high_stakes_page_is_staged_not_live(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        oid = ensure_org(conn, slug="global", name="OGI", parent_slug=None, type="office")
        n, leg = ingest_office_page(conn, org_id=oid,
                                    url="https://www.njit.edu/global/opt-cpt",
                                    title="OPT and CPT",
                                    text="OPT application steps: file Form I-765 within the deadline. " * 8)
    assert leg == "staged"
    row = conn.execute("SELECT is_active,json_extract(metadata,'$.stakes') s "
                       "FROM knowledge_items LIMIT 1").fetchone()
    assert row["is_active"] == 0 and row["s"] == "high"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_office_ingest.py -q`
Expected: FAIL — `ModuleNotFoundError: ...office_ingest`.

- [ ] **Step 3: Implement the hybrid ingest**

```python
# v2/core/ingestion/office_ingest.py
"""Hybrid office-prose ingest. Generic prose → chunk-embed as type='office_page' (live).
High-stakes procedural pages (OPT/CPT/I-20, deadlines, billing/$-amounts) → STAGED
(is_active=0, stakes='high') for human sign-off, never auto-live ungrounded. spec §4.3 [RA4]."""
from __future__ import annotations

import re
import sqlite3

from v2.core.ingestion.gsa_docs import upsert_doc_items

_HIGH_STAKES_URL = re.compile(r"opt|cpt|i-?20|i-?765|sevis|visa|deadline|tuition|bursar|"
                              r"billing|fee|payment|refund", re.I)
_DOLLAR = re.compile(r"\$\s?\d")


def is_high_stakes(url: str, text: str) -> bool:
    if _HIGH_STAKES_URL.search(url or ""):
        return True
    if _DOLLAR.search(text or "") and re.search(r"due|deadline|pay|owe|balance", text or "", re.I):
        return True
    return False


def _slug_from_url(url: str) -> str:
    tail = url.rstrip("/").rsplit("/", 1)[-1] or "index"
    return "office/" + re.sub(r"[^a-z0-9]+", "-", tail.lower()).strip("-")[:70]


def ingest_office_page(conn: sqlite3.Connection, *, org_id: int, url: str, title: str,
                       text: str) -> tuple[int, str]:
    """Returns (chunk_count, leg) with leg in {'chunk','staged'}."""
    slug = _slug_from_url(url)
    if is_high_stakes(url, text):
        n = upsert_doc_items(conn, org_id=org_id, slug=slug, title=title, text=text,
                             source_url=url, doc_type="office_page", source="crawler",
                             is_active=0, stakes="high")
        return n, "staged"
    n = upsert_doc_items(conn, org_id=org_id, slug=slug, title=title, text=text,
                         source_url=url, doc_type="office_page", source="crawler",
                         is_active=1)
    return n, "chunk"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest v2/tests/test_office_ingest.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/office_ingest.py v2/tests/test_office_ingest.py
git commit -m "feat(prose-harvest): hybrid office ingest (chunk-embed generic / stage high-stakes) [RA4]"
```

---

### Task 6: Gated CLI — harvest one entry point end-to-end

**Files:**
- Create: `scripts/harvest_office.py`
- Test: `v2/tests/test_harvest_office_cli.py`

**Interfaces:**
- Consumes: `entry_point_store.list_active/mark_crawled`, `web_crawler.crawl_site` (with `relevance_gated=False`) + `fetch_with_status`, `office_quality.{is_low_quality,dedup_boilerplate}`, `office_ingest.ingest_office_page`, `ensure_org`, `sync_org_nodes`, `hardened_backup`.
- Produces: `harvest_entry_point(conn, ep_row, fetch, *, budget=60, depth=3) -> dict` (counts: pages, chunked, staged, dropped); `main(argv)` (gated: dry-run default, `--commit` takes a backup).

- [ ] **Step 1: Write the failing test (pure orchestration, injected fetch)**

```python
# v2/tests/test_harvest_office_cli.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion import entry_point_store as eps
from scripts.harvest_office import harvest_entry_point

PAGES = {
    "https://x.njit.edu/eos/": '<a href="/eos/visitor">Visitor</a><a href="/eos/fees">Fees</a>'
                               '<p>' + "Welcome to EOS. " * 30 + '</p>',
    "https://x.njit.edu/eos/visitor": '<p>' + "Visitor parking is in the Lock Street Deck. " * 20 + '</p>',
    "https://x.njit.edu/eos/fees": '<p>' + "Permit fees are $200 due by Sept 1. " * 20 + '</p>',
}

def _fetch(url):
    return (PAGES.get(url), 200 if url in PAGES else 404)

def test_harvest_chunks_generic_and_stages_high_stakes(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        ensure_org(conn, slug="eos", name="EOS", parent_slug=None, type="office")
        ep = eps.add_seed(conn, url="https://x.njit.edu/eos/", scope_prefix="/eos/",
                          org_slug="eos", parent_slug="njit", org_type="office")
        row = conn.execute("SELECT * FROM crawl_entry_points WHERE id=?", (ep,)).fetchone()
        stats = harvest_entry_point(conn, row, _fetch, budget=10, depth=2)
    assert stats["pages"] >= 2
    assert stats["staged"] >= 1                       # the $-fees page staged
    live = conn.execute("SELECT COUNT(*) c FROM knowledge_items "
                        "WHERE type='office_page' AND is_active=1").fetchone()["c"]
    assert live >= 1                                  # the visitor page is live
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_harvest_office_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: scripts.harvest_office`.

- [ ] **Step 3: Implement the CLI + orchestrator**

```python
# scripts/harvest_office.py
"""Gated harvest of ONE office entry point's prose sub-tree into the KB as type='office_page'.
crawl_site (relevance_gated=False) → quality gate → hybrid ingest. Dry-run default;
--commit takes a hardened backup. Embed afterwards with v2/scripts/embed_all.py. spec Plan A."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.ingestion import entry_point_store as eps
from v2.core.ingestion.office_ingest import ingest_office_page
from v2.core.ingestion.office_quality import dedup_boilerplate, is_low_quality
from v2.core.ingestion.web_crawler import clean_text, crawl_site, fetch_with_status


def harvest_entry_point(conn, ep_row, fetch, *, budget: int = 60, depth: int = 3) -> dict:
    """Crawl one entry point's sub-tree, quality-gate, ingest. fetch(url)->(html|None,status)."""
    seed = ep_row["url"]
    res = crawl_site(seed, lambda u: fetch(u)[0], max_depth=depth, budget=budget,
                     relevance_gated=False)
    pages = dedup_boilerplate([(p.url, p.text) for p in res.pages])
    org_id = ensure_org(conn, slug=ep_row["org_slug"], name=ep_row["org_slug"].upper(),
                        parent_slug=ep_row["parent_slug"], type=ep_row["org_type"])
    stats = {"pages": len(pages), "chunked": 0, "staged": 0, "dropped": 0}
    for url, text in pages:
        if is_low_quality(text):
            stats["dropped"] += 1
            continue
        title = (text.splitlines()[0][:80] if text.strip() else url)
        n, leg = ingest_office_page(conn, org_id=org_id, url=url, title=title, text=text)
        stats["chunked" if leg == "chunk" else "staged"] += 1
    eps.mark_crawled(conn, ep_row["id"])
    sync_org_nodes(conn)
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--entry-id", type=int, help="crawl_entry_points.id to harvest (default: all active)")
    ap.add_argument("--budget", type=int, default=60)
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)

    conn = get_connection(args.db)
    rows = (conn.execute("SELECT * FROM crawl_entry_points WHERE id=?", (args.entry_id,)).fetchall()
            if args.entry_id else eps.list_active(conn, aspect="office"))
    print(f"office harvest: {len(rows)} active entry point(s)")
    if not rows:
        return 0
    if not args.commit:
        print("(dry run — pass --commit; a hardened backup is taken first)")
        return 0
    bkp = hardened_backup(args.db, "pre-office-harvest")
    print(f"backup: {bkp.name}")
    fetch = fetch_with_status()
    with conn:
        for row in rows:
            stats = harvest_entry_point(conn, row, fetch, budget=args.budget, depth=args.depth)
            print(f"  {row['url']}: {stats}")
    print("next: python v2/scripts/embed_all.py  (then review staged high-stakes pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest v2/tests/test_harvest_office_cli.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Full new-suite regression**

Run: `python3 -m pytest v2/tests/test_entry_point_store.py v2/tests/test_office_link_policy.py v2/tests/test_office_quality.py v2/tests/test_office_ingest.py v2/tests/test_harvest_office_cli.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/harvest_office.py v2/tests/test_harvest_office_cli.py
git commit -m "feat(prose-harvest): gated harvest_office CLI (crawl→quality→hybrid ingest)"
```

---

## Self-review (run before execution)

- **Spec coverage (Plan A scope):** registry table+accessor (§4.1, A1) ✓; office link policy + per-entry budget/depth (§4.2 [SE1], A3) ✓; status-surfacing fetch (§4.5 [SE3], A2 — *retire logic itself is Plan C*) ✓; hybrid ingest → `office_page` + high-stakes extract-only-via-staging (§4.3 [RA4], A5) ✓; pre-ingest quality gate (§4.3 [RA5], A4) ✓; gated CLI (§7, A6) ✓. **Deferred to later plans (loudly):** the separate retrieval tier + precedence ladder (§4.4 — **Plan B**); recurrence/404-410 retire + self-extension candidate-write (§4.5/§4.6 — **Plan C**); Wave-1 registration + chat verify + eval (§6/§10 — **Plan D**).
- **Note on the extract leg:** A5 stages high-stakes pages (is_active=0, stakes='high') rather than running `grounded_extract` inline — verbatim grounded-extraction of staged pages is wired in **Plan B/D** (it needs the office-tier + an approve step). A5's guarantee: high-stakes prose is NEVER live-ungrounded. This is the honest-partial boundary; flagged so it is not silently dropped.
- **Placeholder scan:** none — every step has complete code/commands.
- **Type consistency:** `fetch(url) -> (html|None, status|None)` is consistent across A2/A6; `ingest_office_page -> (int, str)` consistent A5/A6; `doc_type='office_page'` → `knowledge_items.type` verified against `gsa_docs.upsert_doc_items`.
```
