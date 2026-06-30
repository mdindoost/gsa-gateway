# Day-1 PROSE Rebuild — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline) — this build is
> shared-state heavy (one canonical write path that all engines call), so tasks are sequential, TDD,
> committed one-by-one. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Rebuild the crawl-sourced PROSE corpus as one canonical row per NJIT page (fullest real
content), losslessly, on a dev copy gated before any live swap — fixing the dedup regression at root and
making recrawls idempotent + change-catching.

**Architecture:** Add ONE shared canonical-prose write path (`canonical_url.py` + a global URL-keyed
upsert) that `college_crawl`/`catalog_crawl`/`www_crawl` all route through; a wipe+rebuild runner; and a
fail-closed, content-aware coverage gate that compares the rebuilt dev DB against the live backup before an
atomic swap.

**Tech Stack:** Python 3.11+, SQLite (+ sqlite-vec), existing crawler stack (`eos_crawl.extract_prose`,
`college_crawl.ingest_*`, `catalog_crawl` sitemap engine), Ollama `nomic-embed-text` embed.

**Spec:** `docs/superpowers/specs/2026-06-30-day1-prose-rebuild-design.md` (rev3, 2 reviews GO-WITH-CHANGES).

## Global Constraints
- Gated live writes: dev copy first; `hardened_backup` before any live write; default dry-run; `--commit`.
- Crawl = data-bringing only; cleaning is MECHANICAL (no rewriting). No serving/gating logic in the crawler.
- PRESERVE byte-identical: `crawler` PERSON rows (`metadata.entity_id IS NOT NULL`), `nodes`/`edges`,
  `scholar`/`dashboard`/`migration` rows, OPS db. WIPE only: `created_by IN ('njit_www_crawl','college_crawl',
  'catalog_crawl')` + (`created_by='crawler' AND entity_id IS NULL`).
- Never insert `search_text` (generated). Embeddings: `search_document:`/`search_query:` prefixes, L2-norm.
- One canonical row per `canonical_prose_url` across ALL prose sources (DB-enforced).
- Commit after every task. Update memory `project_www_crawl_build_b.md` at each task boundary (power-off safety).

---

### Task 1: `canonical_prose_url()` — the one normalizer

**Files:**
- Create: `v2/core/ingestion/canonical_url.py`
- Test: `v2/tests/test_canonical_url.py`

**Interfaces:**
- Produces: `canonical_prose_url(url: str) -> str` (https, lowercase host, strip trailing slash→`/` root,
  drop fragment, KEEP query unless host+path identical and query in a vetted-noise set — default keep).
  Reuses `web_crawler.normalize_url` + `eos_crawl._canon` then strips slash (same as `catalog_crawl._norm`).

- [ ] **Step 1 — failing tests** (`v2/tests/test_canonical_url.py`):
```python
from v2.core.ingestion.canonical_url import canonical_prose_url as C
def test_trailing_slash_and_scheme():
    assert C("http://WWW.njit.edu/registrar/") == "https://www.njit.edu/registrar"
    assert C("https://www.njit.edu/registrar")  == "https://www.njit.edu/registrar"
def test_root_slash_kept():
    assert C("https://www.njit.edu/") == "https://www.njit.edu/"
def test_fragment_dropped_query_kept():
    assert C("https://x.njit.edu/p#sec") == "https://x.njit.edu/p"
    assert C("https://x.njit.edu/p?audience=international") == "https://x.njit.edu/p?audience=international"
def test_idempotent():
    u="https://www.njit.edu/bursar/payment-information"
    assert C(C(u)) == C(u)
```
- [ ] **Step 2 — run, verify fail** (`pytest v2/tests/test_canonical_url.py -q`; FAIL: import error).
- [ ] **Step 3 — implement** `canonical_prose_url` in `canonical_url.py` (mirror `catalog_crawl._norm`,
  keep query, drop fragment). Refactor `catalog_crawl._norm` to delegate to it (no behavior change for the
  no-query catalog URLs).
- [ ] **Step 4 — run, verify pass** (this test + `pytest v2/tests/test_catalog_crawl.py -q` still green).
- [ ] **Step 5 — commit** `feat(prose): canonical_prose_url normalizer (one shared identity)`.

---

### Task 2: alias resolution by `<link rel=canonical>` evidence

**Files:**
- Modify: `v2/core/ingestion/canonical_url.py`
- Test: `v2/tests/test_canonical_url.py`

**Interfaces:**
- Produces: `canonical_link(html: str) -> str | None` — return the `<link rel="canonical">` href if present
  AND it parses to an http(s) URL on an njit.edu host; else None. Caller resolves identity as
  `canonical_prose_url(canonical_link(html) or source_url)`. Ambiguous/missing → fall back to source_url
  (never guess-collapse — spec §4.2).

- [ ] **Step 1 — failing tests:**
```python
from v2.core.ingestion.canonical_url import canonical_link
def test_canonical_link_present():
    html='<html><head><link rel="canonical" href="https://informatics.njit.edu/undergraduate-thesis-option"/></head></html>'
    assert canonical_link(html) == "https://informatics.njit.edu/undergraduate-thesis-option"
def test_canonical_link_absent():
    assert canonical_link("<html><head></head></html>") is None
def test_canonical_link_offsite_ignored():
    assert canonical_link('<link rel="canonical" href="https://example.com/x">') is None
```
- [ ] **Step 2 — run, verify fail.**
- [ ] **Step 3 — implement** `canonical_link` (BeautifulSoup, `find("link", rel="canonical")`, njit-host guard).
- [ ] **Step 4 — run, verify pass.**
- [ ] **Step 5 — commit** `feat(prose): canonical-link alias resolution (ambiguous stays active)`.

---

### Task 3: `prose_quality_len()` — boilerplate-stripped content length (the keep-fullest metric)

**Files:**
- Create: `v2/core/ingestion/prose_quality.py`
- Test: `v2/tests/test_prose_quality.py`

**Interfaces:**
- Produces: `prose_quality_len(content: str) -> int` — token-ish length of content AFTER
  `eos_crawl._strip_recurring_assets`-style boilerplate removal (NOT raw len, NOT type-token ratio).
  `keep_better(a_content, a_type, b_content, b_type) -> bool` (True if A should win over B): never let
  `webpage` beat `policy/news/event`; else higher `prose_quality_len`; tie → caller breaks by recency.

- [ ] **Step 1 — failing tests** (stripped-length picks the page with more real content; webpage never beats policy):
```python
from v2.core.ingestion.prose_quality import prose_quality_len, keep_better
def test_stripped_length_prefers_more_real_content():
    short="Pay your bill online."; long=short+" "+("fee schedule details "*40)
    assert prose_quality_len(long) > prose_quality_len(short)
def test_webpage_never_beats_policy():
    assert keep_better("x"*5000,"webpage","y"*10,"policy") is False
    assert keep_better("y"*5000,"policy","x"*10,"webpage") is True
def test_density_breaks_within_same_type():
    assert keep_better("real "*200,"policy","nav "*5,"policy") is True
```
- [ ] **Step 2 — run, verify fail.**
- [ ] **Step 3 — implement** using `_strip_recurring_assets` (import from `eos_crawl`); length = whitespace-split count.
- [ ] **Step 4 — run, verify pass.**
- [ ] **Step 5 — commit** `feat(prose): keep-fullest-by-stripped-length + never-webpage>policy`.

---

### Task 4: global URL-keyed canonical upsert (the shared write path)

**Files:**
- Create: `v2/core/ingestion/prose_store.py`
- Test: `v2/tests/test_prose_store.py`

**Interfaces:**
- Consumes: Task 1 `canonical_prose_url`, Task 3 `keep_better`/`prose_quality_len`.
- Produces: `upsert_prose(conn, *, org_id, ptype, title, content, meta, canonical, created_by) -> str`
  returning one of `'inserted'|'updated'|'unchanged'|'skipped_worse'`. Idempotency keyed on
  `metadata.natural_key == canonical` **across ALL orgs/sources** (NOT `(org_id,nk,created_by)`):
  - no active row for canonical → INSERT (natural_key=canonical).
  - active row exists, identical content_hash → `unchanged`.
  - active row exists, different content → keep_better decides: if new wins, deactivate old + INSERT new
    (same canonical); else `skipped_worse` (leave existing). Truncation guard = keep_better (shorter loses).

- [ ] **Step 1 — failing tests** (global key; keep-fullest; webpage-vs-policy; idempotent re-run):
```python
# build an in-mem conn via schema.get_connection(':memory:'); create_all; ensure one org.
def test_second_org_same_url_does_not_dup(conn, org_a, org_b):
    upsert_prose(conn, org_id=org_a, ptype="policy", title="T", content="real "*100, meta={}, canonical="https://www.njit.edu/p", created_by="college_crawl")
    r = upsert_prose(conn, org_id=org_b, ptype="webpage", title="T", content="nav "*3, meta={}, canonical="https://www.njit.edu/p", created_by="njit_www_crawl")
    assert r == "skipped_worse"
    assert active_rows_for(conn, "https://www.njit.edu/p") == 1   # one canonical row, the policy one
def test_fuller_replaces_thinner_same_type(conn, org_a):
    upsert_prose(conn, org_id=org_a, ptype="policy", title="T", content="a "*10, meta={}, canonical="U", created_by="c")
    assert upsert_prose(conn, org_id=org_a, ptype="policy", title="T", content="a "*500, meta={}, canonical="U", created_by="c") == "updated"
    assert active_rows_for(conn,"U")==1
def test_rerun_identical_is_unchanged(conn, org_a): ...  # same content twice -> 'unchanged', 1 row
```
- [ ] **Step 2 — run, verify fail.**
- [ ] **Step 3 — implement** `upsert_prose` (SELECT active by natural_key across all rows; keep_better;
  deactivate+insert or skip). Store `metadata.natural_key=canonical`, `content_hash`, `source=created_by`.
- [ ] **Step 4 — run, verify pass.**
- [ ] **Step 5 — commit** `feat(prose): global URL-keyed canonical upsert (keep-fullest, cross-org)`.

---

### Task 5: route the three engines through `upsert_prose` + global PDF dedup

**Files:**
- Modify: `v2/core/ingestion/college_crawl.py` (`ingest_college`:179, `ingest_pdf_pages`:227)
- Modify: `v2/core/ingestion/www_crawl.py` (`run`:247 — drop `filter_existing_content` content-skip;
  resolve identity via Task 1+2; pass a run-global `seen_canon` for PDFs)
- Test: `v2/tests/test_www_crawl.py`, `v2/tests/test_college_crawl.py`

**Interfaces:**
- Consumes: Task 4 `upsert_prose`, Task 1/2 identity.
- `ingest_college`/`ingest_pdf_pages` compute `canonical = canonical_prose_url(canonical_link(html) or url)`
  and call `upsert_prose` instead of their local `(org_id,nk,created_by)` upsert. A run-scoped
  `seen_canon: set` makes a URL/PDF appearing in two sitemaps a no-op the 2nd time (kills the 32 PDF self-dups).

- [ ] **Step 1 — failing tests:** PDF-in-two-sitemaps → 1 row; the `graduate-admissions` webpage-vs-policy
  case → policy row kept; `www_crawl.run` twice on a fixture → 0 inserts/0 dups 2nd run.
- [ ] **Step 2 — run, verify fail.**
- [ ] **Step 3 — implement** the rewiring; delete `filter_existing_content` usage (content-hash skip).
- [ ] **Step 4 — run, verify pass** (full `pytest v2/tests/test_www_crawl.py v2/tests/test_college_crawl.py
  v2/tests/test_catalog_crawl.py -q`).
- [ ] **Step 5 — commit** `feat(prose): route college/catalog/www through canonical upsert + PDF dedup`.

---

### Task 6: prose-scoped partial unique index

**Files:**
- Modify: `v2/core/database/schema.py` (index list ~:443)
- Test: `v2/tests/test_prose_store.py` (add: a 2nd raw INSERT of same canonical raises IntegrityError)

**Interfaces:**
- Produces: `CREATE UNIQUE INDEX IF NOT EXISTS idx_prose_canonical ON knowledge_items(
  json_extract(metadata,'$.natural_key')) WHERE is_active=1 AND created_by IN
  ('njit_www_crawl','college_crawl','catalog_crawl')` — prose-scoped (excludes person/decomposition rows).
  Upsert must dedup-before-insert (Task 4 already does) so the index never throws mid-run.

- [ ] **Step 1 — failing test** (two active prose rows same canonical → IntegrityError).
- [ ] **Step 2 — run, verify fail** (index absent → no error → test fails).
- [ ] **Step 3 — implement** the index; `create_all` applies idempotently.
- [ ] **Step 4 — run, verify pass.**
- [ ] **Step 5 — commit** `feat(prose): DB-enforced one-active-row-per-canonical-prose-url index`.

---

### Task 7: wipe + rebuild runner (dev copy)

**Files:**
- Create: `scripts/rebuild_prose.py`
- Test: `v2/tests/test_rebuild_prose.py`

**Interfaces:**
- Produces: `wipe_prose(conn) -> dict` (deactivate-or-delete the WIPE scope; assert PRESERVE counts
  unchanged); `rebuild(conn, fetch, fetch_bytes, *, limit=0) -> dict` (run college+catalog+www through the
  shared path). CLI: `python scripts/rebuild_prose.py --db /tmp/dev.db [--limit N] [--commit] [--embed]`,
  default dry-run, `hardened_backup` before any `--commit` on a non-dev path.

- [ ] **Step 1 — failing test** (`wipe_prose` on a seeded in-mem DB removes prose rows, leaves a person row
  with entity_id + a nodes row untouched; counts asserted).
- [ ] **Step 2 — run, verify fail.**
- [ ] **Step 3 — implement** `wipe_prose` (scope query) + `rebuild` (call existing engine runners) + CLI.
- [ ] **Step 4 — run, verify pass.**
- [ ] **Step 5 — commit** `feat(prose): wipe+rebuild runner (dev-copy, preserve people/KG)`.

---

### Task 8: content-aware coverage gate + probes

**Files:**
- Create: `scripts/prose_rebuild_gate.py`
- Test: `v2/tests/test_prose_rebuild_gate.py`

**Interfaces:**
- Produces: `coverage_gate(rebuilt_conn, backup_conn) -> dict` with keys `ok: bool`, `missing_urls: list`,
  `thinner_urls: list`, `preserve_ok: bool`, `single_canonical_ok: bool`. Canonicalizes BOTH sides through
  `canonical_prose_url` (+ alias map). FAIL if a backup canonical prose URL is absent from rebuilt (minus a
  reviewed `drop_list`) OR rebuilt stripped-length < backup (tolerance) for a covered URL. Asserts PRESERVE
  counts byte-identical + ≤1 active row per canonical.

- [ ] **Step 1 — failing tests:** (a) a backup URL missing from rebuilt → `ok=False, missing_urls` nonempty;
  (b) a covered URL whose rebuilt row is shorter → `thinner_urls` nonempty, `ok=False`; (c) all-covered +
  same-length + preserve-equal → `ok=True`.
- [ ] **Step 2 — run, verify fail.**
- [ ] **Step 3 — implement** `coverage_gate` (build canonical→quality_len maps both sides; diff).
- [ ] **Step 4 — run, verify pass.**
- [ ] **Step 5 — commit** `feat(prose): content-aware fail-closed coverage gate vs backup`.

---

### Task 9: widen office GUARD + serving probes (retrieval gate)

**Files:**
- Modify: `v2/tests/office_gold.py` (widen `GUARD` to ~10 general non-office queries with stable tokens)
- Create: `scripts/prose_serving_probe.py` (grad-admissions rank-1/2 + no `Graduate Admissions` dup in top-k;
  anti-corank: ≤1 active row per canonical; rank-preservation vs backup over eval+office query set)
- Test: `v2/tests/test_office_routing_gold.py` (unchanged asserts; just more GUARD params)

- [ ] **Step 1** — add ~7 GUARD queries (stable tokens verified against the rebuilt dev DB).
- [ ] **Step 2** — implement `prose_serving_probe.py` (reuse `gold_runner` pattern).
- [ ] **Step 3** — run the probe against the dev DB; record results.
- [ ] **Step 4 — commit** `test(prose): widen GUARD + grad-admissions/anti-corank/rank-preservation probes`.

---

### Task 10: dev-copy rebuild run → gate → (owner-gated) atomic swap

**(operational, not a code task — run after Tasks 1–9 green; owner sign-off before swap)**
- [ ] Build dev copy `cp gsa_gateway.db /tmp/dev_rebuild.db`; `rebuild_prose.py --db /tmp/dev_rebuild.db --commit --embed`.
- [ ] Run `prose_rebuild_gate.py` (coverage + preserve + single-canonical) + the serving probes + `verify_kg.py`
  + `office_gold` + `eval.sh`. ALL must pass.
- [ ] Show owner: gate report (missing=0, thinner=0, preserve byte-identical, gold ≥ baseline). Owner sign-off.
- [ ] `hardened_backup` live → atomic swap dev→live → re-verify on live → merge `feat/www-crawl-buildb` → push.

## Self-Review
- Spec coverage: §2 wipe/preserve→T7; §4.1 normalizer→T1; §4.2 alias→T2; §4.3 global upsert→T4/T5;
  §4.4 keep-fullest→T3/T4; §4.5 PDF dedup→T5; §4.1 index→T6; §5.1 content-aware coverage→T8; §5.4 probes→T9;
  §5.6 idempotence double-run→T5 test; swap→T10. All covered.
- Types consistent: `canonical_prose_url`, `keep_better`, `upsert_prose`, `coverage_gate` used uniformly.
