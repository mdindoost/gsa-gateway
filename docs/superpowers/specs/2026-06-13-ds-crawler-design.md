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
- **`discover_js(faculty_list_url: str, timeout: int = 30) -> list[str]`** —
  launch headless Chromium (Playwright), load the URL, **wait until at least one
  `people.njit.edu/profile` link is present** (with a timeout), get the rendered
  HTML, return `_extract_profiles(html)`. The render wrapper is thin; all parsing
  is in the pure unit.
- **Optional dependency:** import Playwright lazily inside `discover_js`. If it's
  not installed, raise a clear, actionable error
  (`"DS discovery needs Playwright: pip install playwright && playwright install chromium"`).
  CS and everything else are unaffected if Playwright is absent.
- Uses the project UA (`GSA-Gateway-Bot/...`) for the navigation, consistent with
  the static crawler.

## 3. Discovery dispatch + wiring (`scripts/ingest_faculty.py`)

Add one helper and route both call sites through it:

```python
def discover_for(dept, limit):
    if dept.discovery == "js":
        from v2.core.ingestion.js_discovery import discover_js
        urls = discover_js(dept.faculty_list)
        return urls[:limit] if limit else urls
    return discover(limit, dept.faculty_list)   # static (today's path)
```

- **Single-dept path** (currently raises `SystemExit` for non-static, line ~410):
  replace the raise with `urls = discover_for(dept, args.limit)`.
- **`_run_all`** (line ~342): replace `discover(None, dept.faculty_list)` with
  `discover_for(dept, None)`.

So `--department ds` and the all-departments button use the same dispatch; nothing
else in the pipeline changes.

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

## 5. Verification gate (the cross-check oracle)

Before flipping DS to `verified=True`, validate `discover_js` against an
independent **human spot-check** (the original A/B cross-check idea, with a human
oracle since the API couldn't be pinned):

1. Run `discover_js("https://ds.njit.edu/people")` and record the count + the URLs.
2. The maintainer confirms, from the live page, the **approximate faculty count**
   and **2–3 known names**; assert `discover_js` returns that count (±tolerance)
   and includes those names' profiles.
3. Do a dry-run ingest of a couple of DS profiles (`--department ds --limit 2`, no
   `--commit`) to confirm parsing + org resolution to DS (org 7) looks right.
4. Only then set `verified=True` for DS in the registry.

This is also the seed of the planned golden-eval harness (a
discovery-completeness check). It is a **manual gate run once per enabling a
department**, not a per-refresh op.

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
- **Unit:** `discover_js` raises the actionable error when Playwright is absent
  (monkeypatch the import to fail).
- **Integration (live, manual):** the actual Chromium render is verified by the
  §5 verification gate, not in CI (no browser/network in tests).

## 8. Scope / non-goals

**In:** headless discovery for DS (and any `discovery="js"` department), the
dispatch wiring, the `supported()` gate change, the optional Playwright dep, the
verification gate.
**Out:** reverse-engineering the `/search-api` JSON API (dropped — couldn't pin
it); crawling profile pages with headless (the static pipeline already does that);
the golden-eval harness (separate, planned); auto-installing Playwright.

## 9. Risks

- **Playwright footprint** (~a few hundred MB Chromium) on the always-on box and
  on clean installs — mitigated by making it optional and isolated to JS
  departments (CS/most orgs never need it).
- **Headless fragility** — if DS changes their page structure, discovery may need
  the wait-selector updated; the verification gate + the `verified` flag catch this
  before bad data reaches the live KB (a failed/empty discovery = job failure, per
  the existing 0-profiles=fail rule).
- **Runtime** — headless renders **one** listing page per DS refresh (discovery
  only), so the cost is small and bounded.
