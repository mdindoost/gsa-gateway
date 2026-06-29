# NJIT Catalog Crawl (Build A) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest the entire current `catalog.njit.edu` tree (~446 sitemap URLs) into `knowledge_items` as `created_by='catalog_crawl'` prose, so program-requirement queries (e.g. the DS-PhD trigger) answer from the authoritative page.

**Architecture:** Sitemap-driven (not DFS). A new module `v2/core/ingestion/catalog_crawl.py` reuses `college_crawl`/`eos_crawl` extraction + ingest functions; the only existing-code change is an additive `created_by` parameter on `ingest_college`/`ingest_pdf_pages`. URLs are grouped by a static catalog-segment→college org map (else njit root), ingested per-group (memory bounded), then a guarded retirement pass retires pages that left the sitemap.

**Tech Stack:** Python 3.11, stdlib `xml.etree.ElementTree`, BeautifulSoup (via existing extractors), SQLite, pytest.

## Global Constraints

- **Crawl = data-bringing ONLY** — mechanical clean + verbatim text; NO summarizing/rewriting; NO serving/gating/decline logic. (Hard line, 2026-06-23.)
- **Gated live writes** — dev-copy first, `hardened_backup`, dry-run default, `--commit` explicit; then embed; backups rotate. The LIVE commit is owner-gated (show diff → sign-off) — this plan stops at the dev-copy proof.
- **Never insert `search_text`** — generated column.
- **Reconcile is source-scoped** (`created_by`) — never cross-wipe `college_crawl`/`crawler`/`scholar`/`dashboard`.
- **Embeddings**: documents `search_document: ` prefix, queries `search_query: `, L2-normalized; run `embed_all.py` AND `embed_chunks.py` (full paths under `v2/scripts/`).
- **Graph-write transactions**: ingest helpers do NOT commit — the runner owns the transaction.
- **Source of truth**: spec `docs/superpowers/specs/2026-06-29-catalog-crawl-build-a-design.md`.

## File Structure

- **Modify** `v2/core/ingestion/college_crawl.py` — add `created_by: str = PROSE_SOURCE` param to `ingest_college` + `ingest_pdf_pages`; replace all three `PROSE_SOURCE` refs in each (meta `source`, SELECT `created_by=?`, INSERT `created_by`). Additive; existing callers unchanged.
- **Create** `v2/core/ingestion/catalog_crawl.py` — `CATALOG_ORG_MAP`, `org_for`, `catalog_seed_urls`, `extract_urls`, `iter_catalog_groups`, `reconcile_catalog`, `CATALOG_SOURCE`.
- **Create** `scripts/crawl_catalog.py` — gated runner (mirrors `scripts/crawl_college.py`).
- **Create** `v2/tests/test_catalog_crawl.py` — unit tests (no network; injected fetchers; in-memory DB).

---

### Task 1: Additive `created_by` param on the ingest functions (B3)

**Files:**
- Modify: `v2/core/ingestion/college_crawl.py` (`ingest_college` ~179-219, `ingest_pdf_pages` ~222-305)
- Test: `v2/tests/test_catalog_crawl.py`

**Interfaces:**
- Produces: `ingest_college(conn, org_slug, org_name, parent_slug, result, html_by_url, org_type="college", created_by=PROSE_SOURCE)` and `ingest_pdf_pages(conn, org_slug, org_name, parent_slug, pdf_items, fetch_bytes, org_type="college", created_by=PROSE_SOURCE)`.

- [ ] **Step 1: Write the failing test** — idempotency must hold under a NON-default `created_by` (guards the SELECT-binding duplicate-insert trap), and the default path must be unchanged.

```python
# v2/tests/test_catalog_crawl.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import get_connection
from v2.core.ingestion.college_crawl import ingest_college, EntryResult
from v2.core.ingestion.eos_crawl import ProsePage


def _conn():
    c = get_connection(":memory:")
    return c


def _result(url, content, title="T"):
    p = ProsePage(title=title, content=content, source_url=url)
    r = EntryResult(seed="catalog", prose=[p], skipped=[])
    r.html_by_url[url] = "<html></html>"
    return r


def test_ingest_created_by_isolation_and_idempotent():
    conn = _conn()
    url = "https://catalog.njit.edu/graduate/computing-sciences/data-science/data-science-phd"
    # first ingest under a non-default created_by
    out1 = ingest_college(conn, "ywcc", "YWCC", "njit", _result(url, "REQS v1"),
                          {url: "<html></html>"}, org_type="college", created_by="catalog_crawl")
    assert out1["prose_inserted"] == 1
    # re-ingest identical content under SAME created_by → unchanged, NO duplicate insert
    out2 = ingest_college(conn, "ywcc", "YWCC", "njit", _result(url, "REQS v1"),
                          {url: "<html></html>"}, org_type="college", created_by="catalog_crawl")
    assert out2["prose_inserted"] == 0 and out2["prose_unchanged"] == 1
    rows = conn.execute(
        "SELECT created_by, json_extract(metadata,'$.source') FROM knowledge_items "
        "WHERE is_active=1 AND source_url=?", (url,)).fetchall()
    assert rows == [("catalog_crawl", "catalog_crawl")]  # created_by AND meta.source both tracked (N3)
    # changed content version-bumps (old inactive, one active)
    out3 = ingest_college(conn, "ywcc", "YWCC", "njit", _result(url, "REQS v2"),
                          {url: "<html></html>"}, org_type="college", created_by="catalog_crawl")
    assert out3["prose_updated"] == 1
    active = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND source_url=?",
                          (url,)).fetchone()[0]
    assert active == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_catalog_crawl.py::test_ingest_created_by_isolation_and_idempotent -v`
Expected: FAIL — `ingest_college() got an unexpected keyword argument 'created_by'`.

- [ ] **Step 3: Edit `ingest_college`** — add the param and replace all three refs.

Signature:
```python
def ingest_college(conn, org_slug, org_name, parent_slug, result, html_by_url,
                   org_type="college", created_by=PROSE_SOURCE) -> dict:
```
In the `meta` dict: change `"source": PROSE_SOURCE,` → `"source": created_by,`.
In the SELECT existence check: change the bound `PROSE_SOURCE` → `created_by`:
```python
        row = conn.execute(
            "SELECT id, json_extract(metadata,'$.content_hash') FROM knowledge_items "
            "WHERE is_active=1 AND org_id=? AND json_extract(metadata,'$.natural_key')=? "
            "AND created_by=?", (org_id, p.source_url, created_by)).fetchone()
```
In the INSERT: change the trailing `PROSE_SOURCE` value → `created_by`:
```python
        conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
            "version,is_active,created_by) VALUES(?,?,?,?,?,?,1,1,?)",
            (org_id, ptype, p.title, p.content, json.dumps(meta), p.source_url, created_by))
```

- [ ] **Step 4: Edit `ingest_pdf_pages`** — identical treatment.

Signature:
```python
def ingest_pdf_pages(conn, org_slug, org_name, parent_slug, pdf_items, fetch_bytes,
                     org_type="college", created_by=PROSE_SOURCE) -> dict:
```
`meta = {... "source": created_by}`; SELECT `AND created_by=?", (org_id, url, created_by)`; INSERT trailing value `created_by`.

- [ ] **Step 5: Run tests** (new test + existing college_crawl regression)

Run: `python3 -m pytest v2/tests/test_catalog_crawl.py v2/tests/test_college_crawl.py v2/tests/test_recrawl_e2e.py -v`
Expected: PASS (new test green; existing tests unaffected — default `created_by` unchanged).

- [ ] **Step 6: Commit**

```bash
git add v2/core/ingestion/college_crawl.py v2/tests/test_catalog_crawl.py
git commit -m "feat(catalog): add created_by param to college_crawl ingest fns (B3)"
```

---

### Task 2: `CATALOG_ORG_MAP` + `org_for` (hybrid org mapping)

**Files:**
- Create: `v2/core/ingestion/catalog_crawl.py`
- Test: `v2/tests/test_catalog_crawl.py`

**Interfaces:**
- Produces: `CATALOG_SOURCE = "catalog_crawl"`; `org_for(url) -> tuple[str, str, str | None, str]` returning `(org_slug, org_name, parent_slug, org_type)`.

- [ ] **Step 1: Write the failing test**

```python
def test_org_for_maps_college_segments_else_njit():
    from v2.core.ingestion.catalog_crawl import org_for
    assert org_for("https://catalog.njit.edu/graduate/computing-sciences/data-science/data-science-phd")[0] == "ywcc"
    assert org_for("https://catalog.njit.edu/undergraduate/newark-college-engineering/x")[0] == "nce"
    assert org_for("https://catalog.njit.edu/graduate/science-liberal-arts/physics")[0] == "csla"
    assert org_for("https://catalog.njit.edu/graduate/architecture-design/architecture")[0] == "hcad"
    assert org_for("https://catalog.njit.edu/graduate/management/x")[0] == "mtsm"
    assert org_for("https://catalog.njit.edu/undergraduate/honors-college")[0] == "honors"
    # university-wide / unknown → njit root
    assert org_for("https://catalog.njit.edu/graduate/academic-policies-procedures")[0] == "njit"
    assert org_for("https://catalog.njit.edu/graduate/admissions-financial-support")[0] == "njit"
    assert org_for("https://catalog.njit.edu/about-university/accreditation")[0] == "njit"
    assert org_for("https://catalog.njit.edu/programs")[0] == "njit"
    # njit tuple shape
    assert org_for("https://catalog.njit.edu/programs") == ("njit", "New Jersey Institute of Technology", None, "university")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_catalog_crawl.py::test_org_for_maps_college_segments_else_njit -v`
Expected: FAIL — module `catalog_crawl` does not exist.

- [ ] **Step 3: Create `catalog_crawl.py` with the map + resolver**

```python
"""Sitemap-driven crawler for catalog.njit.edu (Build A).

Brings the whole current NJIT catalog into knowledge_items as `catalog_crawl` prose. Reuses
college_crawl/eos_crawl extraction + ingest; the ONLY behavioral seam is the created_by param.
Makes NO serving/gating decisions (data-bringing-only hard line).

Spec: docs/superpowers/specs/2026-06-29-catalog-crawl-build-a-design.md
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, urlunsplit

from v2.core.ingestion.college_crawl import EntryResult, is_people_path
from v2.core.ingestion.eos_crawl import (
    extract_prose, _url_rank, _strip_recurring_assets, _canon,
)
from v2.core.ingestion.web_crawler import normalize_url

logger = logging.getLogger(__name__)

CATALOG_SOURCE = "catalog_crawl"
DEFAULT_SITEMAP = "https://catalog.njit.edu/sitemap.xml"
_SITEMAP_LOC = "{http://www.sitemaps.org/schemas/sitemap/0.9}loc"
_DISALLOW_PREFIXES = ("/archive/",)  # robots-disallowed past-year trees (belt + suspenders)

_NJIT = ("njit", "New Jersey Institute of Technology", None, "university")
# catalog 2nd-level segment -> (org_slug, org_name, parent_slug, org_type). Names match existing orgs.
CATALOG_ORG_MAP: dict[str, tuple[str, str, str, str]] = {
    "computing-sciences":          ("ywcc", "YWCC", "njit", "college"),
    "science-liberal-arts":        ("csla", "College of Science and Liberal Arts", "njit", "college"),
    "newark-college-engineering":  ("nce", "Newark College of Engineering", "njit", "college"),
    "architecture-design":         ("hcad", "Hillier College of Architecture & Design", "njit", "college"),
    "management":                  ("mtsm", "Martin Tuchman School of Management (MTSM)", "njit", "college"),
    "honors-college":              ("honors", "Albert Dorman Honors College", "njit", "college"),
}


def org_for(url: str) -> tuple[str, str, str | None, str]:
    """Map a catalog URL to (org_slug, org_name, parent_slug, org_type) by its 2nd-level path
    segment (after graduate/undergraduate); anything else → njit root."""
    segs = [s for s in urlsplit(url).path.split("/") if s]
    if len(segs) >= 2 and segs[0] in ("graduate", "undergraduate") and segs[1] in CATALOG_ORG_MAP:
        return CATALOG_ORG_MAP[segs[1]]
    return _NJIT
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_catalog_crawl.py::test_org_for_maps_college_segments_else_njit -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/catalog_crawl.py v2/tests/test_catalog_crawl.py
git commit -m "feat(catalog): CATALOG_ORG_MAP + org_for hybrid org resolver"
```

---

### Task 3: `catalog_seed_urls` — sitemap parse, archive-exclusion, normalize-once (B1, S6)

**Files:**
- Modify: `v2/core/ingestion/catalog_crawl.py`
- Test: `v2/tests/test_catalog_crawl.py`

**Interfaces:**
- Consumes: `fetch_bytes(url) -> bytes | None` (from `web_crawler.make_bytes_fetcher`).
- Produces: `catalog_seed_urls(fetch_bytes, sitemap_url=DEFAULT_SITEMAP) -> list[str]` — normalized (https, lowercased host, trailing slash stripped), `/archive/`-excluded, deduped, order-preserved.

- [ ] **Step 1: Write the failing test**

```python
def test_catalog_seed_urls_parses_excludes_archive_normalizes():
    from v2.core.ingestion.catalog_crawl import catalog_seed_urls
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://catalog.njit.edu/graduate/computing-sciences/data-science/data-science-phd/</loc></url>
      <url><loc>http://catalog.njit.edu/undergraduate/management/x/</loc></url>
      <url><loc>https://catalog.njit.edu/archive/2019/old-program/</loc></url>
      <url><loc>   </loc></url>
      <url><loc>https://catalog.njit.edu/programs/</loc></url>
      <url><loc>https://catalog.njit.edu/programs/</loc></url>
    </urlset>"""
    out = catalog_seed_urls(lambda u: xml)
    assert out == [
        "https://catalog.njit.edu/graduate/computing-sciences/data-science/data-science-phd",
        "https://catalog.njit.edu/undergraduate/management/x",   # http→https
        "https://catalog.njit.edu/programs",                      # deduped, slash stripped
    ]


def test_catalog_seed_urls_empty_on_fetch_or_parse_failure():
    from v2.core.ingestion.catalog_crawl import catalog_seed_urls
    assert catalog_seed_urls(lambda u: None) == []          # fetch failed
    assert catalog_seed_urls(lambda u: b"<not xml") == []    # parse failed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_catalog_crawl.py -k catalog_seed_urls -v`
Expected: FAIL — `catalog_seed_urls` not defined.

- [ ] **Step 3: Implement** (append to `catalog_crawl.py`)

```python
def _norm(url: str) -> str:
    """Normalize ONCE: scheme→https + lowercased host (via normalize_url/_canon), then strip the
    trailing slash uniformly. This string is stored as source_url AND compared in retirement —
    nothing re-normalizes downstream (the S6 invariant)."""
    u = _canon(normalize_url(url, url))
    p = urlsplit(u)
    path = p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme, p.netloc, path, "", ""))


def catalog_seed_urls(fetch_bytes, sitemap_url: str = DEFAULT_SITEMAP) -> list[str]:
    """The current canonical catalog frontier from sitemap.xml. Fetched with fetch_bytes
    (make_bytes_fetcher) because make_fetcher rejects application/xml (B1). Drops empties +
    /archive/ (past years); normalizes + dedupes; preserves order."""
    data = fetch_bytes(sitemap_url)
    if not data:
        return []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for loc in root.iter(_SITEMAP_LOC):
        raw = (loc.text or "").strip()
        if not raw:
            continue
        if any(urlsplit(raw).path.startswith(pre) for pre in _DISALLOW_PREFIXES):
            continue
        u = _norm(raw)
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_catalog_crawl.py -k catalog_seed_urls -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/catalog_crawl.py v2/tests/test_catalog_crawl.py
git commit -m "feat(catalog): catalog_seed_urls — sitemap frontier (B1, S6)"
```

---

### Task 4: `extract_urls` — explicit-list extraction (reuses extract_prose, people-skip, dedup)

**Files:**
- Modify: `v2/core/ingestion/catalog_crawl.py`
- Test: `v2/tests/test_catalog_crawl.py`

**Interfaces:**
- Consumes: `fetch(url) -> str | None` (HTML; from `web_crawler.make_fetcher`).
- Produces: `extract_urls(urls, fetch) -> EntryResult` — `.prose` (deduped ProsePages), `.skipped`, `.html_by_url` populated for kept pages; people-paths skipped.

- [ ] **Step 1: Write the failing test**

```python
def test_extract_urls_skips_people_dedups_and_stashes_html():
    from v2.core.ingestion.catalog_crawl import extract_urls
    pages = {
        "https://catalog.njit.edu/a": "<html><body><div role='main'><h1>A</h1><p>Alpha body text here.</p></div></body></html>",
        "https://catalog.njit.edu/b": "<html><body><div role='main'><h1>B</h1><p>Beta body text here.</p></div></body></html>",
        "https://catalog.njit.edu/about-university/directory/faculty": "<html><body><div role='main'><h1>F</h1><p>roster names</p></div></body></html>",
        "https://catalog.njit.edu/c": None,  # fetch failure → skipped
    }
    res = extract_urls(list(pages), lambda u: pages[u])
    kept = {p.source_url for p in res.prose}
    assert kept == {"https://catalog.njit.edu/a", "https://catalog.njit.edu/b"}  # faculty skipped, c failed
    assert "https://catalog.njit.edu/c" in res.skipped
    assert set(res.html_by_url) == kept
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_catalog_crawl.py::test_extract_urls_skips_people_dedups_and_stashes_html -v`
Expected: FAIL — `extract_urls` not defined.

- [ ] **Step 3: Implement** (append to `catalog_crawl.py`)

```python
import hashlib


def extract_urls(urls, fetch) -> EntryResult:
    """Extract prose from an EXPLICIT url list (no DFS). Skips people pages (explore.py owns
    people), dedups by content hash keeping the cleanest alias, stashes raw HTML for date
    extraction. Brings data only; no DB writes."""
    res = EntryResult(seed="catalog", prose=[], skipped=[])
    by_hash: dict[str, object] = {}
    order: list[str] = []
    for url in urls:
        if is_people_path(url):
            continue
        html = fetch(url)
        if not html:
            res.skipped.append(url)
            continue
        page = extract_prose(url, html)
        if page is None:
            res.skipped.append(url)
            continue
        h = hashlib.sha1(page.content.encode("utf-8")).hexdigest()
        if h not in by_hash:
            by_hash[h] = page
            order.append(h)
            res.html_by_url[url] = html
        elif _url_rank(page.source_url) < _url_rank(by_hash[h].source_url):
            res.html_by_url.pop(by_hash[h].source_url, None)
            by_hash[h] = page
            res.html_by_url[url] = html
    res.prose = [by_hash[h] for h in order]
    _strip_recurring_assets(res.prose)
    return res
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_catalog_crawl.py::test_extract_urls_skips_people_dedups_and_stashes_html -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/catalog_crawl.py v2/tests/test_catalog_crawl.py
git commit -m "feat(catalog): extract_urls — explicit-list prose extraction"
```

---

### Task 5: `iter_catalog_groups` — group by org, yield per-group (N1 memory)

**Files:**
- Modify: `v2/core/ingestion/catalog_crawl.py`
- Test: `v2/tests/test_catalog_crawl.py`

**Interfaces:**
- Produces: `iter_catalog_groups(urls, fetch) -> Iterator[tuple[str, str, str | None, str, EntryResult]]` yielding `(org_slug, org_name, parent_slug, org_type, EntryResult)` once per org group (its `EntryResult.html_by_url` is the per-group HTML; the runner ingests then drops it before the next group).

- [ ] **Step 1: Write the failing test**

```python
def test_iter_catalog_groups_groups_by_org():
    from v2.core.ingestion.catalog_crawl import iter_catalog_groups
    urls = [
        "https://catalog.njit.edu/graduate/computing-sciences/x",
        "https://catalog.njit.edu/graduate/computing-sciences/y",
        "https://catalog.njit.edu/graduate/management/z",
        "https://catalog.njit.edu/programs",
    ]
    html = "<html><body><div role='main'><h1>T</h1><p>Body content here.</p></div></body></html>"
    groups = {g[0]: g for g in iter_catalog_groups(urls, lambda u: html)}
    assert set(groups) == {"ywcc", "mtsm", "njit"}
    assert len(groups["ywcc"][4].prose) == 1  # x and y share identical content → deduped to 1
    assert groups["njit"][1] == "New Jersey Institute of Technology"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_catalog_crawl.py::test_iter_catalog_groups_groups_by_org -v`
Expected: FAIL — `iter_catalog_groups` not defined.

- [ ] **Step 3: Implement** (append to `catalog_crawl.py`)

```python
def iter_catalog_groups(urls, fetch):
    """Group urls by org_for, then extract each group. Yields one tuple per org group so the
    runner can ingest + release each group's HTML before the next (bounded peak memory, N1)."""
    groups: dict[tuple, list[str]] = {}
    for u in urls:
        groups.setdefault(org_for(u), []).append(u)
    for (slug, name, parent, otype), group_urls in groups.items():
        res = extract_urls(group_urls, fetch)
        yield slug, name, parent, otype, res
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_catalog_crawl.py::test_iter_catalog_groups_groups_by_org -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/catalog_crawl.py v2/tests/test_catalog_crawl.py
git commit -m "feat(catalog): iter_catalog_groups — per-org grouping"
```

---

### Task 6: `reconcile_catalog` — guarded retirement (B2, S1, S2, sampled-before-ingest)

**Files:**
- Modify: `v2/core/ingestion/catalog_crawl.py`
- Test: `v2/tests/test_catalog_crawl.py`

**Interfaces:**
- Produces: `reconcile_catalog(conn, sitemap_urls, prior_active_count, *, min_floor=300, ratio=0.8) -> dict` with keys `retired`, `skipped_reason`. Retires only `is_active=1 AND created_by='catalog_crawl' AND type='policy'` rows whose `source_url ∉ sitemap_urls`. Skips entirely if `sitemap_urls` empty or `len < max(min_floor, ratio*prior_active_count)`. PDFs never retired (B2).

- [ ] **Step 1: Write the failing test**

```python
def test_reconcile_catalog_retires_policy_keeps_pdf_and_guards():
    from v2.core.ingestion.catalog_crawl import reconcile_catalog
    conn = _conn()
    def ins(url, typ):
        conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
                     "version,is_active,created_by) VALUES(1,?,?,?, '{}', ?,1,1,'catalog_crawl')",
                     (typ, "t", "c", url))
    conn.execute("INSERT INTO organizations(id,slug,name,type) VALUES(1,'njit','NJIT','university')")
    ins("https://catalog.njit.edu/keep", "policy")        # stays in sitemap
    ins("https://catalog.njit.edu/gone", "policy")         # left sitemap → retire
    ins("https://catalog.njit.edu/file.pdf", "pdf")        # pdf → never retire (B2)
    sitemap = ["https://catalog.njit.edu/keep"] + [f"https://catalog.njit.edu/p{i}" for i in range(400)]
    out = reconcile_catalog(conn, sitemap, prior_active_count=2)
    assert out["retired"] == 1
    assert conn.execute("SELECT is_active FROM knowledge_items WHERE source_url=?",
                        ("https://catalog.njit.edu/gone",)).fetchone()[0] == 0
    assert conn.execute("SELECT is_active FROM knowledge_items WHERE source_url=?",
                        ("https://catalog.njit.edu/file.pdf",)).fetchone()[0] == 1  # pdf kept

def test_reconcile_catalog_floor_and_empty_guards():
    from v2.core.ingestion.catalog_crawl import reconcile_catalog
    conn = _conn()
    conn.execute("INSERT INTO organizations(id,slug,name,type) VALUES(1,'njit','NJIT','university')")
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
                 "version,is_active,created_by) VALUES(1,'policy','t','c','{}',"
                 "'https://catalog.njit.edu/x',1,1,'catalog_crawl')")
    assert reconcile_catalog(conn, [], prior_active_count=446)["retired"] == 0          # empty → skip
    assert reconcile_catalog(conn, [f"u{i}" for i in range(50)], prior_active_count=446)["retired"] == 0  # 50 < floor → skip
    assert conn.execute("SELECT is_active FROM knowledge_items WHERE source_url='https://catalog.njit.edu/x'").fetchone()[0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_catalog_crawl.py -k reconcile_catalog -v`
Expected: FAIL — `reconcile_catalog` not defined.

- [ ] **Step 3: Implement** (append to `catalog_crawl.py`)

```python
def reconcile_catalog(conn, sitemap_urls, prior_active_count, *, min_floor=300, ratio=0.8) -> dict:
    """Retire active catalog_crawl POLICY rows whose source_url left the sitemap. PDFs excluded
    (their natural_key is never a <loc> — B2). Guarded: skip on empty or below-floor frontier so a
    partial sitemap fetch never mass-retires (S1). `prior_active_count` is the catalog_crawl/policy
    active count sampled BEFORE this run's ingest (caller passes it)."""
    sitemap = set(sitemap_urls)
    if not sitemap:
        return {"retired": 0, "skipped_reason": "empty_sitemap"}
    floor = max(min_floor, int(ratio * prior_active_count))
    if len(sitemap) < floor:
        logger.warning("reconcile_catalog: frontier %d < floor %d — skipping retirement",
                       len(sitemap), floor)
        return {"retired": 0, "skipped_reason": f"below_floor({len(sitemap)}<{floor})"}
    rows = conn.execute(
        "SELECT id, source_url FROM knowledge_items "
        "WHERE is_active=1 AND created_by=? AND type='policy'", (CATALOG_SOURCE,)).fetchall()
    retired = 0
    for rid, src in rows:
        if src not in sitemap:
            conn.execute("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
                         "WHERE id=?", (rid,))
            retired += 1
    return {"retired": retired, "skipped_reason": None}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_catalog_crawl.py -k reconcile_catalog -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/catalog_crawl.py v2/tests/test_catalog_crawl.py
git commit -m "feat(catalog): reconcile_catalog — guarded source-scoped retirement (B2,S1,S2)"
```

---

### Task 7: Gated runner `scripts/crawl_catalog.py`

**Files:**
- Create: `scripts/crawl_catalog.py`
- Test: (covered by the dev-copy integration run in Task 8; the runner is thin orchestration over tested units)

**Interfaces:**
- Consumes: all of `catalog_crawl` + `college_crawl.ingest_college`/`ingest_pdf_pages` + `web_crawler.make_fetcher`/`make_bytes_fetcher` + `scripts._area_tag_migrate.hardened_backup`.

- [ ] **Step 1: Create the runner**

```python
"""Gated runner for the catalog.njit.edu prose crawler (Build A).

Dry-run by default; --commit writes the live DB (hardened_backup first). Sitemap-driven: the
frontier IS catalog.njit.edu/sitemap.xml. People are owned by explore.py; college subdomain prose
by college_crawl. This owns catalog.njit.edu (created_by='catalog_crawl').

Gated:  cp gsa_gateway.db /tmp/dev.db
        python scripts/crawl_catalog.py --db /tmp/dev.db            # dry-run
        python scripts/crawl_catalog.py --db /tmp/dev.db --commit   # dev write, inspect + verify_kg
        python scripts/crawl_catalog.py --commit --embed             # live (owner-gated)

Spec: docs/superpowers/specs/2026-06-29-catalog-crawl-build-a-design.md
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import get_connection
from v2.core.ingestion.catalog_crawl import (
    CATALOG_SOURCE, catalog_seed_urls, iter_catalog_groups, reconcile_catalog)
from v2.core.ingestion.college_crawl import ingest_college, ingest_pdf_pages
from v2.core.ingestion.web_crawler import make_fetcher, make_bytes_fetcher


def main(argv=None):
    ap = argparse.ArgumentParser(description="catalog.njit.edu prose crawler (Build A)")
    ap.add_argument("--db", default="gsa_gateway.db")
    ap.add_argument("--commit", action="store_true", help="Write live DB (hardened_backup first)")
    ap.add_argument("--embed", action="store_true", help="Run embed_all.py + embed_chunks.py after commit")
    ap.add_argument("--delay", type=float, default=0.3, help="Politeness delay between fetches (s)")
    ap.add_argument("--limit", type=int, default=0, help="Dev: only first N sitemap URLs (forces --no-reconcile)")
    ap.add_argument("--no-reconcile", action="store_true", help="Skip the retirement pass")
    args = ap.parse_args(argv)

    if args.limit:
        args.no_reconcile = True   # S5: a partial frontier must never retire

    if args.commit:
        from scripts._area_tag_migrate import hardened_backup
        hardened_backup(args.db, label="catalog-crawl")

    conn = get_connection(args.db)
    fetch = make_fetcher()
    fetch_bytes = make_bytes_fetcher()

    # Sample the retirement-floor baseline BEFORE ingest (catalog_crawl/policy only).
    prior = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND created_by=? AND type='policy'",
        (CATALOG_SOURCE,)).fetchone()[0]

    urls = catalog_seed_urls(fetch_bytes)
    if not urls:
        print("ERROR: catalog sitemap returned no URLs — aborting (no destructive action taken).")
        sys.exit(2)
    if args.limit:
        urls = urls[:args.limit]
    print(f"frontier: {len(urls)} catalog URLs (prior active catalog policy rows: {prior})")

    def _delayed_fetch(u):
        h = fetch(u)
        if args.delay:
            time.sleep(args.delay)
        return h

    totals = {"prose_inserted": 0, "prose_updated": 0, "prose_unchanged": 0,
              "pdf_inserted": 0, "pdf_updated": 0, "pdf_unchanged": 0, "skipped": 0}
    for slug, name, parent, otype, res in iter_catalog_groups(urls, _delayed_fetch):
        out = ingest_college(conn, slug, name, parent, res, res.html_by_url,
                             org_type=otype, created_by=CATALOG_SOURCE)
        pdf_items = [(u, t) for p in res.prose for u, t in p.files if u.lower().endswith(".pdf")]
        if pdf_items:
            pout = ingest_pdf_pages(conn, slug, name, parent, pdf_items, fetch_bytes,
                                    org_type=otype, created_by=CATALOG_SOURCE)
            for k in ("pdf_inserted", "pdf_updated", "pdf_unchanged"):
                totals[k] += pout[k]
        for k in ("prose_inserted", "prose_updated", "prose_unchanged"):
            totals[k] += out[k]
        totals["skipped"] += out["skipped"]
        print(f"  {slug}: prose +{out['prose_inserted']} ~{out['prose_updated']} "
              f"={out['prose_unchanged']} skipped {out['skipped']}")

    if not args.no_reconcile:
        rec = reconcile_catalog(conn, urls, prior)
        print(f"retirement: {rec}")
    else:
        print("retirement: skipped (--no-reconcile/--limit)")

    print("totals:", totals)

    if args.commit:
        conn.commit()
        print("COMMITTED")
        if args.embed:
            subprocess.run([sys.executable, str(REPO / "v2/scripts/embed_all.py")], check=True)
            subprocess.run([sys.executable, str(REPO / "v2/scripts/embed_chunks.py")], check=True)
    else:
        print("DRY RUN — no commit (use --commit to write)")
    return totals


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-check the CLI parses (no network)**

Run: `python3 scripts/crawl_catalog.py --help`
Expected: prints usage with `--db --commit --embed --delay --limit --no-reconcile`.

- [ ] **Step 3: Commit**

```bash
git add scripts/crawl_catalog.py
git commit -m "feat(catalog): gated runner scripts/crawl_catalog.py"
```

---

### Task 8: Dev-copy integration proof + no-regression measurement (NO live write)

**Files:** none (operational). Produces the evidence the owner signs off on before the live commit.

- [ ] **Step 1: Full unit suite green**

Run: `python3 -m pytest v2/tests/test_catalog_crawl.py v2/tests/test_college_crawl.py v2/tests/test_recrawl_e2e.py -q`
Expected: all PASS.

- [ ] **Step 2: Dev-copy dry-run**

```bash
cp gsa_gateway.db /tmp/dev.db
python3 scripts/crawl_catalog.py --db /tmp/dev.db --delay 0.3 2>&1 | tail -30
```
Expected: `frontier: ~446 catalog URLs`; per-org prose counts printed; `DRY RUN — no commit`.

- [ ] **Step 3: Dev-copy commit + inspect**

```bash
python3 scripts/crawl_catalog.py --db /tmp/dev.db --commit 2>&1 | tail -20
sqlite3 /tmp/dev.db "SELECT o.slug, COUNT(*) FROM knowledge_items k JOIN organizations o ON o.id=k.org_id WHERE k.is_active=1 AND k.created_by='catalog_crawl' GROUP BY o.slug ORDER BY 2 DESC;"
sqlite3 /tmp/dev.db "SELECT type, COUNT(*) FROM knowledge_items WHERE is_active=1 AND created_by='catalog_crawl' GROUP BY type;"
```
Expected: ~400+ policy rows distributed ywcc/csla/nce/hcad/mtsm/honors/njit; type mostly `policy` (+ any `pdf`). Confirm `metadata.source='catalog_crawl'` on a sample row.

- [ ] **Step 4: Content-quality spot-check (S3, N5)**

```bash
sqlite3 /tmp/dev.db "SELECT title, substr(content,1,300) FROM knowledge_items WHERE is_active=1 AND created_by='catalog_crawl' AND source_url LIKE '%data-science-phd%';"
```
Expected: program-specific title ("Ph.D. in Data Science…"); content is the verbatim requirements text, NOT the left-nav program tree. If nav-polluted, STOP and add a CourseLeaf content selector before proceeding (escalate to owner).

- [ ] **Step 5: Isolation check** — other sources untouched on the dev copy.

```bash
for src in college_crawl crawler scholar dashboard; do
  echo -n "$src active: "; sqlite3 /tmp/dev.db "SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND created_by='$src';"
done
```
Expected: counts equal the live DB's pre-crawl counts (no cross-wipe). Compare against `gsa_gateway.db` with the same query.

- [ ] **Step 6: Idempotent re-run** — second dev commit inserts ~0.

```bash
python3 scripts/crawl_catalog.py --db /tmp/dev.db --commit 2>&1 | grep totals
```
Expected: `prose_inserted` ≈ 0, mostly `prose_unchanged`; retirement retired 0.

- [ ] **Step 7: verify_kg on the dev copy**

Run: `python3 scripts/verify_kg.py --db /tmp/dev.db` (if it accepts `--db`; else copy `/tmp/dev.db` to a temp working path it reads).
Expected: no new alignment failures vs the live DB baseline.

- [ ] **Step 8: Embed the dev copy + no-regression gate** (this is the RAG GO-gate; run pre/post)

```bash
python3 v2/scripts/embed_all.py --db /tmp/dev.db 2>/dev/null || python3 v2/scripts/embed_all.py
python3 v2/scripts/embed_chunks.py --db /tmp/dev.db 2>/dev/null || python3 v2/scripts/embed_chunks.py
# (a) office-routing gold — no new dilution regression
python3 -m pytest v2/tests/ -k office_routing_gold -q
# (b) sibling-probe + trigger acceptance via the X-ray (gate ON), DB pointed at the dev copy
GATEWAY_DB=/tmp/dev.db bash scripts/ask.sh "data science phd qualifying exam requirements" --answer
GATEWAY_DB=/tmp/dev.db bash scripts/ask.sh "how many courses for the data science phd" --answer
GATEWAY_DB=/tmp/dev.db bash scripts/ask.sh "computer science phd qualifying exam" --answer   # sibling returns ITS page
GATEWAY_DB=/tmp/dev.db bash scripts/ask.sh "data science qualifying exam" --answer            # underspecified — record outcome
```
Expected: the DS-PhD trigger answers from `…/data-science/data-science-phd`; the CS-PhD probe returns the CS page; record the underspecified-query outcome (feeds the deferred abstain item, not a blocker). *(If `ask.sh`/`embed_*` don't accept a DB override flag, note the exact mechanism the repo uses to point them at `/tmp/dev.db` and use that; do not run against the live DB.)*

- [ ] **Step 9: Present the diff + evidence to the owner.** Per the EXPERT-REVIEW HARD GATE, the LIVE run is owner-gated. Summarize: row counts per org, content spot-check, isolation proof, idempotency, the trigger + sibling probe results. Wait for sign-off before `python3 scripts/crawl_catalog.py --commit --embed` on the live DB + the post-live acceptance re-check.

---

## Self-Review

**1. Spec coverage:**
- Sitemap-driven frontier → Task 3. Hybrid org map → Task 2. `created_by` isolation (B3) → Task 1. Explicit-list extraction + people-skip + dedup → Task 4. Per-group memory bound (N1) → Task 5. Guarded retirement (B2/S1/S2, sampled-before-ingest) → Task 6. Runner with B1 fetch wiring, `--limit`→`--no-reconcile` (S5), embed_all+embed_chunks → Task 7. Chunk pass + no-regression gate + trigger/sibling acceptance + isolation + content spot-check (S3/N5) → Task 8. All spec §9 goals map to a task.
- DEFERRED (correctly, per spec): Build B; the abstain/program-scoping defect (Task 8 only *measures* the residual).

**2. Placeholder scan:** No TBD/TODO. The `_norm` step note flags the single-return final form explicitly. The `ask.sh`/`embed_*` DB-override caveat in Task 8 is an operational instruction, not a code placeholder.

**3. Type consistency:** `org_for` returns a 4-tuple `(slug, name, parent, org_type)` used identically by `iter_catalog_groups` and the runner. `EntryResult` (from `college_crawl`) has `.prose/.skipped/.html_by_url` used consistently. `ingest_college`/`ingest_pdf_pages` `created_by` kw matches Task 1's signature. `reconcile_catalog(conn, sitemap_urls, prior_active_count)` matches the runner call. `CATALOG_SOURCE='catalog_crawl'` used everywhere.
