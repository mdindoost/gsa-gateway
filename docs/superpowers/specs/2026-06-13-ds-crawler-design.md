> **SUPERSEDED (2026-06-13):** the premise was wrong. `ds.njit.edu/people` is a
> 2-card hub page, not the faculty list. The real faculty list,
> `ds.njit.edu/administration-and-faculty`, is **plain static HTML** with the
> `people.njit.edu/profile` links directly — DS never needed a headless browser.
> The headless crawler built from this spec was reverted; DS now uses the existing
> static `discover()` with the corrected registry URL. Kept for the record; the
> verification-gate idea (§5) lives on as the planned golden-eval harness.

# DS Faculty Crawler (headless discovery) — Design Spec

**Goal:** Add Data Science (DS) faculty to the KB. DS's faculty list
(`ds.njit.edu/people`) is a JavaScript-rendered React app, so the static
discovery that works for CS finds **0** profile links. This adds a **headless
discovery** step that renders the page and extracts the `people.njit.edu/profile`
URLs; everything downstream of discovery is unchanged.

**Builds on** the Refresh NJIT KB design (`2026-06-13-refresh-njit-kb-design.md`)
and the department registry / `verified` flag.

---

## 1. Background (what we found)

- `ds.njit.edu/people` is **Drupal + React over Elasticsearch** — the faculty are
  fetched client-side and rendered as `people.njit.edu/profile/<slug>` links. The
  static HTML contains **no** profile links and **no** React-mount params.
- The internal JSON API (`/search-api/...`) could **not** be reliably pinned: the
  exact content-type/index isn't in the page, the proxy 404s without it, and the
  live request couldn't be captured from DevTools. `people.njit.edu` (canonical
  host) is the same Drupal+ES design with no static shortcut or useful sitemap.
- **Decision:** don't depend on the undocumented API. Render the page with a
  **headless browser** and scrape the profile links — robust to NJIT changing
  their internals, and reusable for any future JS-rendered source.
- **Downstream already works:** once we have the `people.njit.edu/profile` URLs,
  the existing parser → decomposer → org-resolver (→ DS org 7) → embed pipeline
  handles the rest. CS proves it. The **only** new piece is discovery.

## 2. New module — `v2/core/ingestion/js_discovery.py`

Two units, separated so the logic is unit-testable without a browser:

- **`_extract_profiles(rendered_html: str) -> list[str]`** — pure function: find
  unique `https://people.njit.edu/profile/<slug>` links in rendered HTML, in
  document order. *(Reuses the same regex shape as `discover()`.)* Unit-tested.
- **`discover_js(faculty_list_url, timeout=30) -> DiscoveryResult`** — launch
  headless Chromium (Playwright), load the URL, and return both the DOM-scraped
  profile list **and** the page's own data response (see §5 cross-check). Details:
  - **Completeness, not "≥1 link" (B3):** waiting for one profile link only proves
    the app started. The list may paginate / lazy-load, so a naive scrape can
    silently return a partial set. Therefore: after load, **scroll / click any
    "load more" / next control until the `people.njit.edu/profile` link count stops
    growing** (poll the count between actions; stop when stable across two polls or
    no pagination control remains), with an overall budget. Then scrape the final
    DOM. If the page exposes a total/result-count element, capture it too for the
    §5 check.
  - **Network interception (gives us the "A" oracle for free):** register a
    Playwright response handler that captures the JSON the page itself fetches for
    the people list (the `/search-api`-style request we couldn't pin by hand — but
    the browser makes it for us). Return its parsed hits alongside the DOM scrape so
    §5 can cross-check the two independent sources automatically.
  - **Failure visibility:** on selector/pagination timeout, **raise** (never silent
    empty) and log the rendered page's **title + HTML length** so the operator can
    distinguish "page structure changed" from "got a consent/bot-challenge shell."
  - **One retry:** retry the whole render once before failing (headless cold-starts
    / transient network flap — mirrors the codebase's "embed, retry once" idiom).
    Set an explicit navigation timeout in addition to the selector-wait `timeout`.
- **Optional dependency, and it must NOT use `SystemExit` (S1):** import Playwright
  lazily inside `discover_js`; if absent, raise a regular `RuntimeError` with an
  actionable message
  (`"DS discovery needs Playwright: pip install playwright && playwright install chromium"`).
  It must be a `RuntimeError`/`ImportError`, **never `raise SystemExit`** — the
  `_run_all` loop catches `Exception` (so a missing dep / render failure degrades to
  a clean per-department failure), but `SystemExit` would escape and abort the whole
  `--all` batch.
- Uses the project UA (`GSA-Gateway-Bot/...`) on the browser context, consistent
  with the static crawler. (Note: a non-browser UA + headless `navigator.webdriver`
  is a bot-fingerprint; NJIT is unlikely to block its own public directory, but a
  challenge/empty-shell is a render-failure mode — see failure visibility above.)

## 3. Discovery dispatch + wiring (`scripts/ingest_faculty.py`)

Add one helper and route both call sites through it:

```python
def discover_for(dept, limit):
    if dept.discovery == "js":
        from v2.core.ingestion.js_discovery import discover_js
        urls = discover_js(dept.faculty_list).urls   # DOM-scraped production set
        return urls[:limit] if limit else urls
    return discover(limit, dept.faculty_list)   # static (today's path)
```

- The ingest path uses only the **DOM-scraped URL list** (`.urls`); the intercepted
  data (§5) is for the verification gate, not production ingest.
- **`--limit` for js is a post-hoc slice** of the fully-rendered list (it does *not*
  bound render time — `discover_js` always renders the whole page). Fine for
  dry-run sampling; documented so no one expects `--limit 2` to make js cheap.
- **Single-dept path** (currently raises `SystemExit` for non-static, line ~410):
  replace the raise with `urls = discover_for(dept, args.limit)`.
- **`_run_all`** (line ~342): replace `discover(None, dept.faculty_list)` with
  `discover_for(dept, None)`.

So `--department ds` and the all-departments button use the same dispatch; nothing
else in the pipeline changes. *(Verified call sites: `ingest_faculty.py` lines
~342 and ~414 are the complete set; the legacy `scripts/crawl_cs_faculty.py` has
its own separate `discover()` and is not part of this pipeline.)*

## 4. `supported()` — verification is the gate, not discovery method

Today `supported()` = `discovery == "static" AND verified` — which would exclude DS
even after we can crawl it. Change it so **`verified` is the gate** and the
discovery method is just a dispatch detail:

```python
def supported():
    return [d for d in DEPARTMENTS.values() if d.verified]
```

DS stays `verified=False` (so the button still ignores it) until the verification
gate below passes; then flipping `verified=True` adds it to the button
automatically. CS remains the only verified department until then.

**Test updates ship with this change (B1):** `bot/tests/test_departments.py`
currently asserts `supported() == {static AND verified}` and comments "ds not in
keys — JS-rendered." Both become wrong once DS is verified. Rewrite them in this
change: assert `supported() == {verified}` and update the DS rationale to
"verified=False (not yet validated)" rather than "js." Re-check `test_ingest_all.py`
(it injects its own departments, so it should be unaffected — confirm).

## 5. Verification gate (the cross-check oracle)

Before flipping DS to `verified=True`, validate completeness via **two independent
oracles** — the automated cross-check (B's DOM scrape vs A's intercepted JSON) plus
a human spot-check focused on catching truncation:

1. Run `discover_js("https://ds.njit.edu/people")`. It returns both the DOM-scraped
   URLs **and** the page's own intercepted data response (§2).
2. **Automated cross-check (the "A" oracle, free):** assert the DOM-scraped set ==
   the set derived from the intercepted JSON (same profiles). Agreement of two
   independent methods is strong evidence the scrape is complete, not partial.
3. **Human completeness check (catches pagination truncation, B3):** the maintainer
   confirms the **full advertised faculty count** from the live page; assert
   `discover_js` returns that exact count (tight tolerance, not "approximate"). Spot-
   check a name that sorts **last alphabetically** (e.g. a W/Z surname) — this
   specifically catches a list truncated to the first page.
4. **Org check (B2):** dry-run ingest a couple of DS profiles
   (`--department ds --limit 2`, no `--commit`) and confirm they resolve to **DS =
   org 6** (`Data Science`, slug `data-science`, which exists in the DB). The
   resolver maps by page label/slug and falls back to `default_org_id=6` only if
   unresolved — confirm the label resolves so profiles aren't silently bucketed by
   fallback.
5. Only then set `verified=True` for DS in the registry.

This is also the seed of the planned golden-eval harness (a discovery-completeness
check). It is a **manual gate run once per enabling a department**, not a per-refresh
op.

## 6. Dependency

- Add `playwright` to `requirements.txt` as an **optional** extra (documented as
  needed only for JS departments), plus the one-time `playwright install chromium`.
- Document in `docs/admin_guide.md` / `LOCAL_SERVER.md` that DS refresh requires
  Playwright on the server.

## 7. Testing

- **Unit:** `_extract_profiles` on a saved **rendered-HTML fixture** of
  `ds.njit.edu/people` (deterministic, no browser) — asserts it finds the expected
  profile slugs and dedupes.
- **Unit:** `discover_for` dispatch — `discovery=="js"` calls `discover_js`
  (monkeypatched), `static` calls `discover` (monkeypatched); `--all`/`--department`
  unaffected for CS.
- **Unit:** `discover_js` raises an actionable **`RuntimeError`** (never `SystemExit`)
  when Playwright is absent (monkeypatch the import to fail) — so `_run_all` catches it.
- **Unit:** the DOM-scrape vs intercepted-JSON cross-check comparison (feed both a
  fixture DOM and a fixture JSON response; assert the set-equality logic).
- **Unit:** `test_departments.py` rewrite for the `verified`-only `supported()` (B1).
- **Integration (live, manual):** the actual Chromium render + pagination + the §5
  verification gate, not in CI (no browser/network in tests).

## 8. Scope / non-goals

**In:** headless discovery for DS (and any `discovery="js"` department), the
dispatch wiring, the `supported()` gate change, the optional Playwright dep, the
verification gate (incl. capturing the page's own data response via Playwright
network interception, used as the automated cross-check oracle).
**Out:** *hand* reverse-engineering the `/search-api` endpoint (we let the browser
make the call and intercept it instead); crawling profile pages with headless (the
static pipeline already does that); the golden-eval harness (separate, planned);
auto-installing Playwright; replacing Chromium with a direct `httpx` call to the
intercepted endpoint (a future optimization once the endpoint shape is known — see
§9 — not built now).

## 9. Risks

- **Playwright footprint** (~a few hundred MB Chromium) on the always-on box and
  on clean installs — mitigated by making it optional and isolated to JS
  departments (CS/most orgs never need it).
- **Headless fragility** — if DS changes their page structure, discovery may need
  the wait/pagination logic updated; the verification gate + the `verified` flag
  catch this before bad data reaches the live KB (a failed/empty discovery = job
  failure, per the existing 0-profiles=fail rule). On render failure the module
  logs page title + HTML length to distinguish a structure change from a
  consent/bot-challenge shell.
- **Partial/paginated list (the main correctness risk)** — mitigated by the
  scroll/load-more-until-stable logic (§2) **and** the two-oracle completeness check
  (§5: DOM scrape == intercepted JSON, plus a human full-count + last-alphabetical
  spot-check). This is the one that produces silently-wrong data if skipped.
- **Playwright footprint** (~few-hundred-MB Chromium) — see §6; optional, isolated.
  **Future optimization:** because §2 already intercepts the page's real data
  endpoint, once its shape is known we *could* drop Chromium and call that endpoint
  directly with `httpx` (lightweight). Not built now; noted so it isn't lost.
- **Runtime** — headless renders **one** listing page per DS refresh (discovery
  only), so the cost is small and bounded.
