# Prose Harvest — Plan C: Recurrence + Self-Extension — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make office harvest a repeatable, change-detected, self-healing op (no manual ops): re-crawl active entry points on their interval, re-embed ONLY changed pages, retire a page ONLY on a confirmed 404/410 (never on a transient/empty crawl), and register newly-discovered office hubs as `candidate` entry points for gated activation.

**Architecture:** A small `office_page_state(url → content_hash, last_seen)` table powers change detection (skip unchanged pages, so re-runs don't churn embeddings) and the retire bookkeeping. A status-aware retire pass (using the `fetch_with_status` from Plan A) deactivates only confirmed-gone pages. A pure hub-link classifier turns same-host out-of-scope section roots into `candidate` rows via the existing `entry_point_store.upsert_candidate`. A recurring gated CLI selects due entry points and runs crawl → change-detect ingest → retire → discover.

**Tech Stack:** Python 3.11, SQLite (STRICT), the Plan A/B modules (`harvest_entry_point`, `office_ingest`, `entry_point_store`, `fetch_with_status`), `hashlib`, `bs4`. Tests: pytest.

## Global Constraints

- **Source/type-scoped writes:** all reads/writes target `type='office_page'` + `created_by='crawler'` only — never touch curated/people rows. — spec §4.5
- **Retire ONLY on a confirmed 404/410.** A transport error (`fetch_with_status` returns status `None`: timeout/DNS/SSRF-block) MUST NOT retire. NEVER retire on an EMPTY crawl (zero pages = transient failure), mirroring the people-crawler's empty-decomposition guard. — spec §4.5 [SE3]
- **Change detection:** re-ingest/re-embed a page ONLY when its cleaned-text content hash changed; unchanged pages are a no-op (existing rows + vectors untouched). — spec §4.5
- **Self-extension is GATED:** discovered hubs are written as `status='candidate'` with provenance and do NOT auto-activate; the owner/a gated step activates them (`entry_point_store.activate`). — spec §4.6 [D3]
- **Gated live writes:** dry-run default; `--commit` takes a `hardened_backup` first; core helpers don't commit (the CLI owns the transaction). — repo invariant
- **`crawl_interval_days` is owner-set per entry point** (already a column); the job exposes it, never hardcodes a cadence. — spec D4
- HARD GATE: built TDD; diffs shown for sign-off; nothing merged to main / no restart without owner approval.

---

## File structure

- **Modify** `v2/core/database/schema.py` — add the STRICT `office_page_state` table to the `_TABLE_DDL` list.
- **Modify** `v2/core/ingestion/office_ingest.py` — add `content_hash`, change-detection in `ingest_office_page` (+ optional `entry_point_id`), `retire_404`, `discover_candidate_hubs`.
- **Modify** `scripts/harvest_office.py` — `harvest_entry_point` records seen URLs, passes `entry_point_id`, runs retire + discover; the orchestration both CLIs share.
- **Create** `scripts/recrawl_offices.py` — the recurring gated CLI (select due entry points → harvest → retire → discover).
- **Create/Modify** tests: `v2/tests/test_office_change_detection.py`, `v2/tests/test_office_retire.py`, `v2/tests/test_office_self_extension.py`, `v2/tests/test_recrawl_offices.py`, and update `v2/tests/test_harvest_office_cli.py`.

---

### Task 1: `office_page_state` table + content-hash change detection

**Files:**
- Modify: `v2/core/database/schema.py` (`_TABLE_DDL`)
- Modify: `v2/core/ingestion/office_ingest.py`
- Test: `v2/tests/test_office_change_detection.py`

**Interfaces:**
- Produces:
  - `content_hash(text: str) -> str` — sha256 hex of the page's cleaned text (stripped).
  - `ingest_office_page(conn, *, org_id, url, title, text, entry_point_id=None) -> tuple[int, str]` — now returns `leg ∈ {'chunk','staged','unchanged'}`. On `'unchanged'` (state hash matches) it re-ingests NOTHING (existing rows/vectors intact) and only bumps `last_seen_at`; otherwise it ingests AND upserts the state row.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_office_change_detection.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion.office_ingest import content_hash, ingest_office_page

URL = "https://www.njit.edu/parking/visitor-parking"
TEXT = "Visitor parking is available in the Lock Street Deck. " * 8


def _org(conn):
    with conn:
        return ensure_org(conn, slug="eos", name="EOS", parent_slug=None, type="office")


def test_content_hash_is_stable_and_text_sensitive():
    assert content_hash(TEXT) == content_hash(TEXT)
    assert content_hash(TEXT) != content_hash(TEXT + " Updated.")


def test_unchanged_page_is_skipped_on_reingest(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    oid = _org(conn)
    with conn:
        n1, leg1 = ingest_office_page(conn, org_id=oid, url=URL, title="Visitor Parking", text=TEXT)
    assert leg1 == "chunk" and n1 >= 1
    rows1 = conn.execute("SELECT COUNT(*) c FROM knowledge_items WHERE type='office_page'").fetchone()["c"]
    with conn:
        n2, leg2 = ingest_office_page(conn, org_id=oid, url=URL, title="Visitor Parking", text=TEXT)
    assert leg2 == "unchanged" and n2 == 0
    rows2 = conn.execute("SELECT COUNT(*) c FROM knowledge_items WHERE type='office_page'").fetchone()["c"]
    assert rows2 == rows1                              # no churn — same active rows


def test_changed_page_reingests_and_updates_hash(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    oid = _org(conn)
    with conn:
        ingest_office_page(conn, org_id=oid, url=URL, title="Visitor Parking", text=TEXT)
        n2, leg2 = ingest_office_page(conn, org_id=oid, url=URL, title="Visitor Parking",
                                      text=TEXT + " New permit info added.")
    assert leg2 == "chunk" and n2 >= 1
    h = conn.execute("SELECT content_hash FROM office_page_state WHERE url=?", (URL,)).fetchone()[0]
    assert h == content_hash(TEXT + " New permit info added.")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_office_change_detection.py -q`
Expected: FAIL — `ImportError: cannot import name 'content_hash'` and no `office_page_state` table.

- [ ] **Step 3: Add the table to `schema.py`**

In `v2/core/database/schema.py`, add to the `_TABLE_DDL` list (next to `crawl_entry_points`):

```sql
CREATE TABLE IF NOT EXISTS office_page_state (
    url             TEXT    PRIMARY KEY,
    entry_point_id  INTEGER,
    content_hash    TEXT    NOT NULL,
    last_seen_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
) STRICT;
```

- [ ] **Step 4: Implement `content_hash` + change detection in `office_ingest.py`**

Add at the top of `office_ingest.py` (after imports):

```python
import hashlib


def content_hash(text: str) -> str:
    """Stable sha256 of a page's cleaned text — the change-detection key."""
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()
```

Change `ingest_office_page` to detect unchanged pages and maintain `office_page_state`:

```python
def ingest_office_page(conn, *, org_id, url, title, text, entry_point_id=None):
    """Returns (chunk_count, leg) with leg in {'chunk','staged','unchanged'}."""
    h = content_hash(text)
    prior = conn.execute("SELECT content_hash FROM office_page_state WHERE url=?", (url,)).fetchone()
    if prior is not None and prior[0] == h:
        conn.execute("UPDATE office_page_state SET last_seen_at=datetime('now') WHERE url=?", (url,))
        return 0, "unchanged"                          # no re-ingest, no embedding churn
    slug = _slug_from_url(url)
    if is_high_stakes(url, text):
        n = upsert_doc_items(conn, org_id=org_id, slug=slug, title=title, text=text,
                             source_url=url, doc_type="office_page", source="crawler",
                             is_active=0, stakes="high")
        leg = "staged"
    else:
        n = upsert_doc_items(conn, org_id=org_id, slug=slug, title=title, text=text,
                             source_url=url, doc_type="office_page", source="crawler", is_active=1)
        leg = "chunk"
    conn.execute(
        "INSERT INTO office_page_state(url,entry_point_id,content_hash) VALUES(?,?,?) "
        "ON CONFLICT(url) DO UPDATE SET content_hash=excluded.content_hash, "
        "entry_point_id=excluded.entry_point_id, last_seen_at=datetime('now')",
        (url, entry_point_id, h))
    return n, leg
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest v2/tests/test_office_change_detection.py v2/tests/test_office_ingest.py -q`
Expected: PASS (the new change-detection tests AND the existing Plan A ingest tests — a fresh DB never hits the unchanged branch on first ingest).

- [ ] **Step 6: Commit**

```bash
git add v2/core/database/schema.py v2/core/ingestion/office_ingest.py v2/tests/test_office_change_detection.py
git commit -m "feat(prose-harvest): office_page_state + content-hash change detection (skip unchanged) [SE3-recurrence]"
```

---

### Task 2: Retire a page only on a confirmed 404/410

**Files:**
- Modify: `v2/core/ingestion/office_ingest.py`
- Test: `v2/tests/test_office_retire.py`

**Interfaces:**
- Consumes: `fetch_with_status`-style `fetch(url) -> (html|None, status|None)`.
- Produces: `retire_404(conn, *, org_id, fetch, seen_urls: set[str]) -> dict` — for each active `office_page` URL of this org that was NOT seen in the current crawl, fetch it; retire (`is_active=0` + drop its `office_page_state`) ONLY if status ∈ {404, 410}. Returns `{'checked':int,'retired':int}`. If `seen_urls` is empty (empty crawl) it is a NO-OP (`{'checked':0,'retired':0}`).

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_office_retire.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion.office_ingest import ingest_office_page, retire_404

A = "https://www.njit.edu/parking/a"
B = "https://www.njit.edu/parking/b"


def _setup(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        oid = ensure_org(conn, slug="eos", name="EOS", parent_slug=None, type="office")
        ingest_office_page(conn, org_id=oid, url=A, title="A", text="Parking A info. " * 10)
        ingest_office_page(conn, org_id=oid, url=B, title="B", text="Parking B info. " * 10)
    return conn, oid


def _active(conn, url):
    return conn.execute("SELECT COUNT(*) c FROM knowledge_items WHERE source_url=? AND is_active=1",
                        (url,)).fetchone()["c"]


def test_confirmed_404_retires_unseen_page(tmp_path):
    conn, oid = _setup(tmp_path)
    fetch = lambda u: (None, 404)                       # B is gone
    with conn:
        stats = retire_404(conn, org_id=oid, fetch=fetch, seen_urls={A})
    assert stats["retired"] == 1
    assert _active(conn, A) >= 1 and _active(conn, B) == 0


def test_transient_error_does_not_retire(tmp_path):
    conn, oid = _setup(tmp_path)
    fetch = lambda u: (None, None)                      # timeout/DNS — status unknown
    with conn:
        stats = retire_404(conn, org_id=oid, fetch=fetch, seen_urls={A})
    assert stats["retired"] == 0 and _active(conn, B) >= 1


def test_empty_crawl_never_retires(tmp_path):
    conn, oid = _setup(tmp_path)
    fetch = lambda u: (None, 404)
    with conn:
        stats = retire_404(conn, org_id=oid, fetch=fetch, seen_urls=set())   # empty crawl
    assert stats == {"checked": 0, "retired": 0}
    assert _active(conn, A) >= 1 and _active(conn, B) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_office_retire.py -q`
Expected: FAIL — `ImportError: cannot import name 'retire_404'`.

- [ ] **Step 3: Implement `retire_404`**

Add to `office_ingest.py`:

```python
def retire_404(conn, *, org_id, fetch, seen_urls):
    """Deactivate office_page docs that are CONFIRMED gone (HTTP 404/410). Source/type-scoped.
    NEVER retires on an empty crawl (seen_urls empty = transient failure) or a transport error
    (status None). spec §4.5 [SE3]."""
    if not seen_urls:
        return {"checked": 0, "retired": 0}
    existing = [r[0] for r in conn.execute(
        "SELECT DISTINCT source_url FROM knowledge_items "
        "WHERE type='office_page' AND created_by='crawler' AND org_id=? AND is_active=1 "
        "AND source_url IS NOT NULL", (org_id,)).fetchall()]
    checked = retired = 0
    for url in existing:
        if url in seen_urls:
            continue                                    # just successfully crawled — alive
        checked += 1
        _html, status = fetch(url)
        if status in (404, 410):
            conn.execute("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
                         "WHERE source_url=? AND type='office_page' AND created_by='crawler'", (url,))
            conn.execute("DELETE FROM office_page_state WHERE url=?", (url,))
            retired += 1
    return {"checked": checked, "retired": retired}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest v2/tests/test_office_retire.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/office_ingest.py v2/tests/test_office_retire.py
git commit -m "feat(prose-harvest): retire office pages only on confirmed 404/410 (never on empty/transient) [SE3]"
```

---

### Task 3: Self-extension — discover candidate office hubs

**Files:**
- Modify: `v2/core/ingestion/office_ingest.py`
- Test: `v2/tests/test_office_self_extension.py`

**Interfaces:**
- Produces: `discover_candidate_hubs(seed_url: str, html: str, registered_urls: set[str]) -> list[str]` — same-host links whose path is a **top-level section root** (`/<segment>/`, one segment), excluding: the seed's own scope, anything already in `registered_urls`, assets, and non-section deep paths. Pure (no I/O). Caller writes each via `entry_point_store.upsert_candidate`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_office_self_extension.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.ingestion.office_ingest import discover_candidate_hubs

SEED = "https://www.njit.edu/parking/"
HTML = """
<a href="/bursar/">Bursar</a>
<a href="https://www.njit.edu/global/">Global</a>
<a href="/parking/visitor-parking">Visitor Parking (deep, in scope)</a>
<a href="/parking/">self</a>
<a href="/registrar/deadlines/spring">deep, not a section root</a>
<a href="https://external.example.com/dining/">external host</a>
<a href="/style.css">asset</a>
"""


def test_discovers_unregistered_section_roots_only():
    out = set(discover_candidate_hubs(SEED, HTML, registered_urls={"https://www.njit.edu/global/"}))
    assert "https://www.njit.edu/bursar/" in out          # new top-level section root
    assert "https://www.njit.edu/global/" not in out      # already registered
    assert not any("visitor-parking" in u for u in out)   # deep / in-scope
    assert not any("/parking/" == u.split("njit.edu")[-1] for u in out)  # seed self
    assert not any("registrar/deadlines" in u for u in out)  # deep, not a section root
    assert not any("external.example.com" in u for u in out)
    assert not any(u.endswith(".css") for u in out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_office_self_extension.py -q`
Expected: FAIL — `ImportError: cannot import name 'discover_candidate_hubs'`.

- [ ] **Step 3: Implement `discover_candidate_hubs`**

Add to `office_ingest.py` (reuse `web_crawler` primitives):

```python
import re as _re
from bs4 import BeautifulSoup
from bs4.exceptions import ParserRejectedMarkup
from v2.core.ingestion.web_crawler import is_non_html, normalize_url, same_site, scope_prefix
from urllib.parse import urlparse as _urlparse

_SECTION_ROOT = _re.compile(r"^/[a-z0-9][a-z0-9-]*/?$")   # exactly one path segment


def discover_candidate_hubs(seed_url, html, registered_urls):
    """Same-host top-level section roots (/<segment>/) linked from this page that are NOT the
    seed's own scope and NOT already registered — candidate office hubs for gated activation."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except ParserRejectedMarkup:
        return []
    seed_scope = scope_prefix(seed_url)
    reg = set(registered_urls)
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("mailto:", "javascript:", "tel:", "#")):
            continue
        url = normalize_url(href, seed_url)
        if not url.startswith("http") or not same_site(seed_url, url) or is_non_html(url):
            continue
        path = _urlparse(url).path or "/"
        if not _SECTION_ROOT.match(path):
            continue
        if path.startswith(seed_scope) or url in reg or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest v2/tests/test_office_self_extension.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/office_ingest.py v2/tests/test_office_self_extension.py
git commit -m "feat(prose-harvest): self-extension — discover candidate office hubs (gated) [D3]"
```

---

### Task 4: Recurring re-crawl CLI + wire change-detect/retire/discover into the harvest op

**Files:**
- Modify: `scripts/harvest_office.py` (`harvest_entry_point`: pass `entry_point_id`, collect seen URLs, run retire + discover)
- Create: `scripts/recrawl_offices.py`
- Test: `v2/tests/test_recrawl_offices.py`; update `v2/tests/test_harvest_office_cli.py`

**Interfaces:**
- Consumes: `harvest_entry_point` (extended), `entry_point_store.{list_active,upsert_candidate}`, `retire_404`, `discover_candidate_hubs`, `fetch_with_status`, `hardened_backup`.
- Produces:
  - `harvest_entry_point(conn, ep_row, fetch, *, budget=60, depth=3) -> dict` — now also returns `unchanged`, `retired`, `candidates` counts; passes `entry_point_id`; runs `retire_404` (with the crawl's seen URLs) and `discover_candidate_hubs` (writing candidates via `upsert_candidate`).
  - `due_entry_points(conn, now=None) -> list` — active office entry points whose `crawl_interval_days` has elapsed since `last_crawled_at` (or never crawled).
  - `scripts/recrawl_offices.py main(argv)` — gated CLI: harvest the due entry points, print stats, embed-after note.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_recrawl_offices.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion import entry_point_store as eps
from scripts.recrawl_offices import due_entry_points


def test_due_selects_never_crawled_and_stale_only(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        ensure_org(conn, slug="eos", name="EOS", parent_slug=None, type="university")
        never = eps.add_seed(conn, url="https://www.njit.edu/parking/", scope_prefix="/parking/",
                             org_slug="eos", parent_slug="njit", org_type="office",
                             crawl_interval_days=7)
        fresh = eps.add_seed(conn, url="https://www.njit.edu/global/", scope_prefix="/global/",
                             org_slug="eos", parent_slug="njit", org_type="office",
                             crawl_interval_days=7)
        conn.execute("UPDATE crawl_entry_points SET last_crawled_at=datetime('now') WHERE id=?", (fresh,))
        stale = eps.add_seed(conn, url="https://www.njit.edu/bursar/", scope_prefix="/bursar/",
                             org_slug="eos", parent_slug="njit", org_type="office",
                             crawl_interval_days=7)
        conn.execute("UPDATE crawl_entry_points SET last_crawled_at=datetime('now','-30 days') WHERE id=?", (stale,))
    due_ids = {r["id"] for r in due_entry_points(conn)}
    assert never in due_ids and stale in due_ids and fresh not in due_ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_recrawl_offices.py -q`
Expected: FAIL — `ModuleNotFoundError: scripts.recrawl_offices`.

- [ ] **Step 3: Extend `harvest_entry_point` in `scripts/harvest_office.py`**

Replace the body of `harvest_entry_point` so it records seen URLs, passes `entry_point_id`, and runs retire + discover:

```python
from v2.core.ingestion.office_ingest import (
    discover_candidate_hubs, ingest_office_page, retire_404)


def harvest_entry_point(conn, ep_row, fetch, *, budget: int = 60, depth: int = 3) -> dict:
    """Crawl one entry point: crawl_site → quality gate → change-detected ingest → retire 404s →
    discover candidate hubs. fetch(url)->(html|None,status)."""
    seed = ep_row["url"]
    res = crawl_site(seed, lambda u: fetch(u)[0], max_depth=depth, budget=budget, relevance_gated=False)
    pages = dedup_boilerplate([(p.url, p.text) for p in res.pages])
    row = conn.execute("SELECT id FROM organizations WHERE slug=?", (ep_row["org_slug"],)).fetchone()
    if not row:
        raise ValueError(f"org slug {ep_row['org_slug']!r} not found — create the office org before harvesting it")
    org_id = row[0]
    stats = {"pages": len(pages), "chunked": 0, "staged": 0, "unchanged": 0, "dropped": 0,
             "retired": 0, "candidates": 0}
    seen_urls: set[str] = set()
    for url, text in pages:
        if is_low_quality(text):
            stats["dropped"] += 1
            continue
        seen_urls.add(url)
        title = (text.splitlines()[0][:80] if text.strip() else url)
        _n, leg = ingest_office_page(conn, org_id=org_id, url=url, title=title, text=text,
                                     entry_point_id=ep_row["id"])
        stats[leg] += 1                                 # 'chunk' | 'staged' | 'unchanged'
    stats["retired"] = retire_404(conn, org_id=org_id, fetch=fetch, seen_urls=seen_urls)["retired"]
    # self-extension: classify the hub's outbound links into candidate office hubs (gated)
    hub_html, _ = fetch(seed)
    if hub_html:
        registered = {r[0] for r in conn.execute("SELECT url FROM crawl_entry_points").fetchall()}
        for cand in discover_candidate_hubs(seed, hub_html, registered):
            eps.upsert_candidate(conn, url=cand, discovered_from_url=seed)
            stats["candidates"] += 1
    eps.mark_crawled(conn, ep_row["id"])
    sync_org_nodes(conn)
    return stats
```

- [ ] **Step 4: Create `scripts/recrawl_offices.py`**

```python
#!/usr/bin/env python
"""Recurring gated re-crawl of office entry points whose crawl_interval has elapsed. Reuses
harvest_entry_point (crawl → change-detected ingest → 404 retire → candidate discovery). Dry-run
default; --commit takes a hardened backup. Embed afterwards. spec §4.5/§4.6 Plan C."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from scripts.harvest_office import harvest_entry_point
from v2.core.database.schema import get_connection
from v2.core.ingestion.web_crawler import fetch_with_status


def due_entry_points(conn, now=None):
    """Active office entry points due for re-crawl: never crawled, or last_crawled_at older than
    crawl_interval_days. Entry points with a NULL interval are excluded (no recurrence configured)."""
    return conn.execute(
        "SELECT * FROM crawl_entry_points WHERE status='active' AND aspect='office' "
        "AND crawl_interval_days IS NOT NULL "
        "AND (last_crawled_at IS NULL OR "
        "     julianday('now') - julianday(last_crawled_at) >= crawl_interval_days) "
        "ORDER BY id").fetchall()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--budget", type=int, default=60)
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)

    conn = get_connection(args.db)
    rows = due_entry_points(conn)
    print(f"recrawl: {len(rows)} entry point(s) due")
    for r in rows:
        print("   ", r["url"])
    if not rows:
        return 0
    if not args.commit:
        print("(dry run — pass --commit; a hardened backup is taken first)")
        return 0
    bkp = hardened_backup(args.db, "pre-office-recrawl")
    print(f"backup: {bkp.name}")
    fetch = fetch_with_status()
    with conn:
        for r in rows:
            print(f"  {r['url']}: {harvest_entry_point(conn, r, fetch, budget=args.budget, depth=args.depth)}")
    print("next: python v2/scripts/embed_all.py  (then review staged high-stakes pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Update the existing harvest CLI test**

In `v2/tests/test_harvest_office_cli.py`, the orchestrator test now exercises retire + discover. Update its fetch fake so `fetch(seed)` returns the hub HTML on the re-fetch and any unknown URL returns `(None, 404)`; assert the existing keys still hold and the new keys exist:

```python
def test_harvest_chunks_generic_and_stages_high_stakes(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        ensure_org(conn, slug="eos", name="EOS", parent_slug=None, type="office")
        ep = eps.add_seed(conn, url="https://x.njit.edu/eos/", scope_prefix="/eos/",
                          org_slug="eos", parent_slug="njit", org_type="office")
        row = conn.execute("SELECT * FROM crawl_entry_points WHERE id=?", (ep,)).fetchone()
        stats = harvest_entry_point(conn, row, _fetch, budget=10, depth=2)
    assert stats["pages"] >= 2
    assert stats["staged"] >= 1
    assert "retired" in stats and "candidates" in stats and "unchanged" in stats
    live = conn.execute("SELECT COUNT(*) c FROM knowledge_items "
                        "WHERE type='office_page' AND is_active=1").fetchone()["c"]
    assert live >= 1
```
(Keep the module's existing `PAGES`/`_fetch` helpers; ensure `_fetch("https://x.njit.edu/eos/")` returns the hub HTML tuple so the discover re-fetch succeeds. `_fetch` already returns `(html, 200)` for known URLs and `(None, 404)` for unknown — which is exactly what retire/discover need.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest v2/tests/test_recrawl_offices.py v2/tests/test_harvest_office_cli.py v2/tests/test_office_change_detection.py v2/tests/test_office_retire.py v2/tests/test_office_self_extension.py -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/harvest_office.py scripts/recrawl_offices.py v2/tests/test_recrawl_offices.py v2/tests/test_harvest_office_cli.py
git commit -m "feat(prose-harvest): recurring re-crawl CLI; wire change-detect/retire/discover into the harvest op [D4]"
```

---

## Self-review (run before execution)

- **Spec coverage (Plan C, §4.5/§4.6):** per-URL content hash + change detection / re-embed only changed (Task 1) ✓; retire only on confirmed 404/410, never empty/transient, source-scoped (Task 2) ✓ [SE3]; self-extension candidate rows, gated (Task 3) ✓ [D3]; recurring change-detected re-crawl gated CLI + owner-set `crawl_interval` (Task 4) ✓ [D4].
- **Deferred (loudly):** the **dashboard "Data Sources" BUTTON** for the recurring job is NOT built here — `recrawl_offices.py` is the CLI/engine; wiring it into the existing `local_server` job runner (the same subprocess pattern as the crawler/scholar jobs) is a thin follow-up, flagged for Plan D or a dashboard pass. The **gated ACTIVATION UI** for `candidate` rows is likewise CLI/`entry_point_store.activate`-only here (owner activates); a dashboard review list is a follow-up. The actual **Wave-1 content harvest + chat verify + eval** is **Plan D**.
- **Placeholder scan:** none — full code in every step.
- **Type consistency:** `ingest_office_page(..., entry_point_id=None) -> (int, leg)` with `leg ∈ {'chunk','staged','unchanged'}` consistent across Tasks 1/4; `retire_404(...) -> {'checked','retired'}` and `fetch(url)->(html,status)` consistent Tasks 2/4; `discover_candidate_hubs(seed,html,registered)->list[str]` consistent Tasks 3/4. `office_page_state.url` PK matches the upsert + retire + change-detect reads.
```
