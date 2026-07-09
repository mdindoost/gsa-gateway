# FacultyFolio Multi-College Architecture — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalize the FacultyFolio generator from single-college (hardwired YWCC) to a multi-college site (NJIT hub → college hub → dept leaderboard, flat `/p/<slug>` profiles) with an explicit published-colleges registry, a scoping CLI, ancestor-consistent scoped writes, and SEO files.

**Architecture:** Extend the three existing named level-builders in `build.py` (add `build_college_hub` + `build_njit_hub`) plus a scope-aware orchestrator + argparse CLI. All output paths flow through `paths.py` (the URL seam). Canonical/sitemap URLs use a new `config.SITE_ORIGIN`. College-total counts use a new subtree-distinct `db.college_coverage`. No framework — static Python + Jinja, unchanged.

**Tech Stack:** Python 3.11, Jinja2, SQLite (read-only KG), pytest. Deploys to GitHub Pages (static files).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-09-facultyfolio-multicollege-architecture-design.md`. Every task honors it.
- Profiles stay at `/p/<slug>.html` — **no rename**, no `/p/` redirects (D2).
- Data fidelity: read-only DB (`connect()` opens `mode=ro`); the generator never writes the DB.
- Scoped-write safety (D4): a scoped run writes its scope + regenerates ancestor hubs, **never** writes a sibling college/dept file, **never** wipes a directory.
- SEO URLs are **absolute**: `config.SITE_ORIGIN + <path>` (D6/C1). `SITE_ORIGIN = "https://facultyfolio.github.io"`.
- `PUBLISHED_COLLEGES = ["ywcc"]` today; iteration order == registry order (deterministic).
- Only YWCC is built/tested here. MTSM/HCAD = seams only. Stale-pruning deferred.
- Tests use self-relative assertions vs the live DB (no hardcoded citation/count magic numbers).
- Commits: stage explicit paths only (never `git add -A` — repo holds live-secret `.env.*`). No Claude/co-author attribution in messages.
- Run tests from repo root with `python3 -m pytest facultyfolio/tests/<file> -q`.

---

## File structure

- `facultyfolio/config.py` — **modify**: add `PUBLISHED_COLLEGES`, `SITE_ORIGIN`; extend `LEGACY_REDIRECTS`.
- `facultyfolio/paths.py` — **modify**: nested `leaderboard_path`, new `college_hub_path`/`njit_hub_path`/`sitemap_path`/`robots_path`, `canonical_url`, `rel_root`.
- `facultyfolio/db.py` — **modify**: add `college_coverage`.
- `facultyfolio/render.py` — **modify**: thread explicit `asset_root` + `canonical`; generalize `render_hub` (title + eyebrow) for both hub levels.
- `facultyfolio/templates/base.html` — **modify**: add `<link rel="canonical">`.
- `facultyfolio/templates/hub.html` — **modify**: parameterize eyebrow.
- `facultyfolio/seo.py` — **create**: `sitemap_xml(urls)`, `robots_txt()`.
- `facultyfolio/build.py` — **modify**: new hub builders, threaded canonical/asset_root, scope-aware orchestrator, ancestor refresh, redirect-guard fix, SEO emit, argparse CLI.
- `facultyfolio/tests/` — **create/extend**: `test_paths.py`, `test_db_college_coverage.py`, `test_render_hub.py`, `test_seo.py`, `test_build_scoped.py`.

---

### Task 1: Config — registry, site origin, redirects

**Files:**
- Modify: `facultyfolio/config.py`
- Test: `facultyfolio/tests/test_config_multicollege.py` (create)

**Interfaces:**
- Produces: `config.PUBLISHED_COLLEGES: list[str]`, `config.SITE_ORIGIN: str`, extended `config.LEGACY_REDIRECTS: dict[str,str]` (old_segment → target segment-path, no leading slash/origin).

- [ ] **Step 1: Write the failing test**

```python
# facultyfolio/tests/test_config_multicollege.py
from facultyfolio import config

def test_published_colleges_is_ordered_list_with_ywcc():
    assert isinstance(config.PUBLISHED_COLLEGES, list)
    assert config.PUBLISHED_COLLEGES == ["ywcc"]

def test_site_origin_is_absolute_no_trailing_slash():
    assert config.SITE_ORIGIN.startswith("https://")
    assert not config.SITE_ORIGIN.endswith("/")

def test_legacy_redirects_target_ywcc_nested_segments_no_leading_slash():
    r = config.LEGACY_REDIRECTS
    assert r["computer-science"] == "ywcc/computer-science"
    assert r["data-science"] == "ywcc/data-science"
    assert r["informatics"] == "ywcc/informatics"
    assert r["cs"] == "ywcc/computer-science"
    for target in r.values():
        assert not target.startswith("/") and "://" not in target
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest facultyfolio/tests/test_config_multicollege.py -q`
Expected: FAIL (`PUBLISHED_COLLEGES`/`SITE_ORIGIN` missing; `LEGACY_REDIRECTS` still `{"cs": "computer-science"}`).

- [ ] **Step 3: Edit config.py**

Replace the `COLLEGE_SLUG`/`LEGACY_REDIRECTS` block (currently `config.py:21-25`):

```python
# --- published-colleges registry (ordered; iteration order = registry order) ---
# A college is served only when its slug is here. Departments are auto-discovered
# per college from the KG. Add a slug (after eyeballing) to publish a new college.
PUBLISHED_COLLEGES = ["ywcc"]

# Absolute site origin for canonical <link> + sitemap + robots (SEO needs absolute URLs).
SITE_ORIGIN = "https://facultyfolio.github.io"

# Legacy URL segment -> new target segment-path (NO leading slash, NO origin: the
# redirect stub composes it as ../{target}/index.html relative to /{old}/index.html).
LEGACY_REDIRECTS = {
    "cs": "ywcc/computer-science",
    "computer-science": "ywcc/computer-science",
    "data-science": "ywcc/data-science",
    "informatics": "ywcc/informatics",
}
```

Keep `CS_ORG_ID` / `KOUTIS_NODE` (test anchors). Leave `COLLEGE_NAMES` as-is.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest facultyfolio/tests/test_config_multicollege.py -q`
Expected: PASS.

- [ ] **Step 5: Confirm nothing else referenced the removed `COLLEGE_SLUG`**

Run: `grep -rn "COLLEGE_SLUG" facultyfolio/`
Expected: only `build.py` (fixed in Task 7). If a test references it, note it for Task 7. Do not fix build.py yet.

- [ ] **Step 6: Commit**

```bash
git add facultyfolio/config.py facultyfolio/tests/test_config_multicollege.py
git commit -m "feat(facultyfolio): published-colleges registry + SITE_ORIGIN + nested legacy redirects"
```

---

### Task 2: paths.py — nested layout, canonical, asset-root

**Files:**
- Modify: `facultyfolio/paths.py`
- Test: `facultyfolio/tests/test_paths.py` (create)

**Interfaces:**
- Consumes: `config.SITE_ORIGIN`.
- Produces:
  - `profile_path(out_root, slug)` (unchanged) → `<out>/p/<slug>.html`
  - `leaderboard_path(out_root, college_seg, dept_seg)` → `<out>/<college>/<dept>/index.html`
  - `college_hub_path(out_root, college_seg)` → `<out>/<college>/index.html`
  - `njit_hub_path(out_root)` → `<out>/index.html`
  - `sitemap_path(out_root)` → `<out>/sitemap.xml`; `robots_path(out_root)` → `<out>/robots.txt`
  - `redirect_path(out_root, old_segment)` (unchanged) → `<out>/<old>/index.html`
  - `assets_dir(out_root)` (unchanged)
  - `rel_root(depth)` → `""` for depth 0, `"../"*depth` otherwise (asset_root by nesting depth)
  - `canonical_url(rel_path)` → `SITE_ORIGIN + "/" + rel_path` (rel_path has no leading slash; a hub dir path ends `/`)

- [ ] **Step 1: Write the failing test**

```python
# facultyfolio/tests/test_paths.py
import os
from facultyfolio import paths, config

OUT = "/tmp/ffout"

def test_profile_path_unchanged():
    assert paths.profile_path(OUT, "koutis") == os.path.join(OUT, "p", "koutis.html")

def test_leaderboard_path_is_nested_under_college():
    assert paths.leaderboard_path(OUT, "ywcc", "computer-science") == \
        os.path.join(OUT, "ywcc", "computer-science", "index.html")

def test_college_and_njit_hub_paths():
    assert paths.college_hub_path(OUT, "ywcc") == os.path.join(OUT, "ywcc", "index.html")
    assert paths.njit_hub_path(OUT) == os.path.join(OUT, "index.html")

def test_sitemap_and_robots_paths():
    assert paths.sitemap_path(OUT) == os.path.join(OUT, "sitemap.xml")
    assert paths.robots_path(OUT) == os.path.join(OUT, "robots.txt")

def test_rel_root_by_depth():
    assert paths.rel_root(0) == ""
    assert paths.rel_root(1) == "../"
    assert paths.rel_root(2) == "../../"

def test_canonical_url_is_absolute():
    assert paths.canonical_url("p/koutis.html") == config.SITE_ORIGIN + "/p/koutis.html"
    assert paths.canonical_url("ywcc/computer-science/") == \
        config.SITE_ORIGIN + "/ywcc/computer-science/"
    assert paths.canonical_url("") == config.SITE_ORIGIN + "/"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest facultyfolio/tests/test_paths.py -q`
Expected: FAIL (new functions missing; `leaderboard_path` has the old 2-arg signature).

- [ ] **Step 3: Rewrite paths.py**

```python
"""Output-path single source of truth (URL seam).

Multi-college layout: NJIT hub at /index.html, college hub at /<college>/index.html,
dept leaderboard at /<college>/<dept>/index.html, profiles flat at /p/<slug>.html.
Canonical + sitemap URLs are absolute (config.SITE_ORIGIN + path).
"""
import os

from . import config


def profile_path(out_root: str, slug: str) -> str:
    return os.path.join(out_root, "p", f"{slug}.html")


def leaderboard_path(out_root: str, college_seg: str, dept_seg: str) -> str:
    return os.path.join(out_root, college_seg, dept_seg, "index.html")


def college_hub_path(out_root: str, college_seg: str) -> str:
    return os.path.join(out_root, college_seg, "index.html")


def njit_hub_path(out_root: str) -> str:
    return os.path.join(out_root, "index.html")


def sitemap_path(out_root: str) -> str:
    return os.path.join(out_root, "sitemap.xml")


def robots_path(out_root: str) -> str:
    return os.path.join(out_root, "robots.txt")


def redirect_path(out_root: str, old_segment: str) -> str:
    return os.path.join(out_root, old_segment, "index.html")


def assets_dir(out_root: str) -> str:
    return os.path.join(out_root, "assets")


def rel_root(depth: int) -> str:
    """asset_root for a page `depth` directory levels below the site root."""
    return "../" * depth


def canonical_url(rel_path: str) -> str:
    """Absolute canonical URL. rel_path has no leading slash; a directory ends with '/'."""
    return f"{config.SITE_ORIGIN}/{rel_path}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest facultyfolio/tests/test_paths.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add facultyfolio/paths.py facultyfolio/tests/test_paths.py
git commit -m "feat(facultyfolio): nested paths + canonical/asset-root helpers"
```

---

### Task 3: db.college_coverage — subtree-distinct counts

**Files:**
- Modify: `facultyfolio/db.py`
- Test: `facultyfolio/tests/test_db_college_coverage.py` (create)

**Interfaces:**
- Consumes: `db.org_node_by_slug`, `db.dept_orgs_of_college`, `config.SUPPRESSED`.
- Produces: `db.college_coverage(college_node_id) -> (n_with_scholar: int, m_total: int)` — DISTINCT home-faculty person ids across the college node itself AND all its faculty>0 child orgs. `n` = distinct people whose Scholar `citations` is an int.

- [ ] **Step 1: Write the failing test**

```python
# facultyfolio/tests/test_db_college_coverage.py
from facultyfolio import db, rank

def _ywcc():
    return db.org_node_by_slug("ywcc")

def test_college_coverage_returns_two_ints():
    n, m = db.college_coverage(_ywcc())
    assert isinstance(n, int) and isinstance(m, int)
    assert 0 <= n <= m

def test_college_coverage_is_distinct_not_dept_sum():
    """Distinct people <= sum of dept coverages (dup-home faculty counted once)."""
    ywcc = _ywcc()
    depts = db.dept_orgs_of_college(ywcc)
    dept_sum_m = sum(rank.coverage(d["node_id"])[1] for d in depts)
    n, m = db.college_coverage(ywcc)
    assert m <= dept_sum_m           # distinct never exceeds the naive sum
    assert m >= max(rank.coverage(d["node_id"])[1] for d in depts)  # at least the biggest dept
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest facultyfolio/tests/test_db_college_coverage.py -q`
Expected: FAIL (`AttributeError: module 'facultyfolio.db' has no attribute 'college_coverage'`).

- [ ] **Step 3: Add college_coverage to db.py** (place after `dept_orgs_of_college`)

```python
def college_coverage(college_node_id) -> tuple:
    """(N distinct home faculty with Scholar citations, M distinct home faculty) across the
    college node itself and every faculty>0 child org. DISTINCT by person id, so a faculty
    homed in two child orgs (the known dup-home case) is counted once."""
    import json
    org_ids = [college_node_id] + [d["node_id"] for d in dept_orgs_of_college(college_node_id)]
    conn = connect()
    try:
        placeholders = ",".join("?" for _ in org_ids)
        rows = conn.execute(
            f"""SELECT DISTINCT n.id AS id, n.key AS key, n.attrs AS attrs FROM nodes n
                JOIN edges e ON e.src_id=n.id
                WHERE n.type='Person' AND n.is_active=1
                  AND e.type='has_role' AND e.category='faculty'
                  AND e.dst_id IN ({placeholders}) AND e.is_active=1""",
            org_ids,
        ).fetchall()
    finally:
        conn.close()
    m, n = 0, 0
    for r in rows:
        slug = r["key"].split("/")[-1]
        if slug in config.SUPPRESSED:
            continue
        m += 1
        attrs = json.loads(r["attrs"]) if r["attrs"] else {}
        sch = (attrs.get("profiles", {}) or {}).get("scholar", {}) or {}
        if isinstance(sch.get("citations"), int):
            n += 1
    return n, m
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest facultyfolio/tests/test_db_college_coverage.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add facultyfolio/db.py facultyfolio/tests/test_db_college_coverage.py
git commit -m "feat(facultyfolio): db.college_coverage (subtree-distinct, dup-home-safe)"
```

---

### Task 4: render — canonical + explicit asset_root + generalized hub

**Files:**
- Modify: `facultyfolio/render.py`, `facultyfolio/templates/base.html`, `facultyfolio/templates/hub.html`
- Test: `facultyfolio/tests/test_render_hub.py` (create)

**Interfaces:**
- Consumes: `paths.canonical_url`.
- Produces:
  - `render_hub(title, cards, *, eyebrow, asset_root, canonical) -> str` (generalized: used for BOTH the NJIT hub and each college hub — one template).
  - `render_leaderboard(...)` and `render_profile(...)` gain optional `asset_root` and `canonical` kwargs (default `asset_root="../"`, `canonical=None`) threaded into the template context.
  - base.html renders `<link rel="canonical" href="{{ canonical }}">` when `canonical` is set.

- [ ] **Step 1: Write the failing test**

```python
# facultyfolio/tests/test_render_hub.py
from facultyfolio import render

CARDS = [{"name": "Computer Science", "faculty": 57, "scholar": 39, "url": "computer-science/index.html"}]

def test_hub_renders_title_eyebrow_and_canonical():
    html = render.render_hub("New Jersey Institute of Technology", CARDS,
                             eyebrow="University", asset_root="",
                             canonical="https://facultyfolio.github.io/")
    assert "New Jersey Institute of Technology" in html
    assert "University" in html
    assert '<link rel="canonical" href="https://facultyfolio.github.io/">' in html
    assert 'href="assets/style.css"' in html          # asset_root="" at site root

def test_college_hub_uses_parent_asset_root():
    html = render.render_hub("Ying Wu College of Computing", CARDS,
                             eyebrow="College", asset_root="../",
                             canonical="https://facultyfolio.github.io/ywcc/")
    assert 'href="../assets/style.css"' in html
    assert "computer-science/index.html" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest facultyfolio/tests/test_render_hub.py -q`
Expected: FAIL (`render_hub` takes `(college_name, cards)`; no eyebrow/canonical).

- [ ] **Step 3a: Edit base.html** — add canonical in `<head>` after the title line (`base.html:6`):

```html
<title>{% block title %}FacultyFolio{% endblock %}</title>
{% if canonical %}<link rel="canonical" href="{{ canonical }}">{% endif %}
<link rel="stylesheet" href="{{ asset_root }}assets/style.css">
```

- [ ] **Step 3b: Edit hub.html** — replace the hardcoded eyebrow (`hub.html:14`) `<p class="eyebrow">College</p>` with:

```html
      <p class="eyebrow">{{ eyebrow }}</p>
```

- [ ] **Step 3c: Edit render.py `render_hub`** — replace the function (`render.py:189-193`):

```python
def render_hub(title: str, cards: list, *, eyebrow: str, asset_root: str,
               canonical: str = None) -> str:
    """Hub landing page (NJIT hub: cards=colleges; college hub: cards=depts). One template.
    `asset_root` = rel path to assets/ for this page's depth; `eyebrow` = 'University'/'College'."""
    return _env.get_template("hub.html").render(
        college_name=title, eyebrow=eyebrow, cards=cards,
        asset_root=asset_root, canonical=canonical)
```

- [ ] **Step 3d: Thread asset_root + canonical into leaderboard + profile.**
In `render_leaderboard` (`render.py:253`) change the signature to add kwargs and pass them:

```python
def render_leaderboard(org_name: str, roster_views: dict, stats: dict,
                       coverage: tuple, photo_map: dict, rising=None,
                       asset_root: str = "../", canonical: str = None) -> str:
```
and add `asset_root=asset_root, canonical=canonical,` to the `.render(...)` call's kwargs.

In `render_profile` (`render.py:156`) add `asset_root: str = "../", canonical: str = None` params and put `"asset_root": asset_root, "canonical": canonical,` into the `ctx` dict before `.render(**ctx)`.

(The `_env.globals["asset_root"] = "../"` default stays as a fallback but every builder now passes it explicitly — Task 6.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest facultyfolio/tests/test_render_hub.py facultyfolio/tests/ -q`
Expected: PASS, and the existing render tests still green (canonical is optional; asset_root default preserved).

- [ ] **Step 5: Commit**

```bash
git add facultyfolio/render.py facultyfolio/templates/base.html facultyfolio/templates/hub.html facultyfolio/tests/test_render_hub.py
git commit -m "feat(facultyfolio): canonical link + explicit asset_root + generalized hub render"
```

---

### Task 5: SEO — sitemap.xml + robots.txt

**Files:**
- Create: `facultyfolio/seo.py`
- Test: `facultyfolio/tests/test_seo.py` (create)

**Interfaces:**
- Consumes: `config.SITE_ORIGIN`.
- Produces: `seo.sitemap_xml(abs_urls: list[str]) -> str`; `seo.robots_txt() -> str`.

- [ ] **Step 1: Write the failing test**

```python
# facultyfolio/tests/test_seo.py
from facultyfolio import seo, config

def test_sitemap_lists_absolute_urls():
    xml = seo.sitemap_xml([config.SITE_ORIGIN + "/", config.SITE_ORIGIN + "/p/koutis.html"])
    assert xml.startswith("<?xml")
    assert "<urlset" in xml
    assert f"<loc>{config.SITE_ORIGIN}/</loc>" in xml
    assert f"<loc>{config.SITE_ORIGIN}/p/koutis.html</loc>" in xml

def test_robots_allows_all_and_points_to_sitemap():
    txt = seo.robots_txt()
    assert "User-agent: *" in txt
    assert "Allow: /" in txt
    assert f"Sitemap: {config.SITE_ORIGIN}/sitemap.xml" in txt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest facultyfolio/tests/test_seo.py -q`
Expected: FAIL (`No module named 'facultyfolio.seo'`).

- [ ] **Step 3: Create seo.py**

```python
"""SEO artifacts — sitemap.xml + robots.txt. All URLs absolute (config.SITE_ORIGIN)."""
from xml.sax.saxutils import escape

from . import config


def sitemap_xml(abs_urls: list) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in abs_urls:
        lines.append(f"  <url><loc>{escape(u)}</loc></url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def robots_txt() -> str:
    return ("User-agent: *\n"
            "Allow: /\n"
            f"Sitemap: {config.SITE_ORIGIN}/sitemap.xml\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest facultyfolio/tests/test_seo.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add facultyfolio/seo.py facultyfolio/tests/test_seo.py
git commit -m "feat(facultyfolio): sitemap.xml + robots.txt (absolute URLs)"
```

---

### Task 6: build.py — hub builders + threaded canonical/asset_root

**Files:**
- Modify: `facultyfolio/build.py`
- Test: covered by Task 9 integration (no isolated unit test — these are thin orchestration wrappers; a unit test here would duplicate Task 9).

**Interfaces:**
- Consumes: `paths.*`, `db.college_coverage`, `db.dept_orgs_of_college`, `db.org_node_by_slug`, `db.college_name`, `seo.*`, `render.*`.
- Produces (module-level, used by Task 7):
  - `build_dept(org, out_root, college_seg, photo_map=None) -> str` (now takes `college_seg`, writes nested path, threads `asset_root="../../"` + canonical).
  - `build_college_hub(college_node, college_seg, out_root) -> str`.
  - `build_njit_hub(out_root) -> str`.
  - `build_one(slug, out_root, photo_ref=None) -> str` (unchanged path; now passes `asset_root="../"` + canonical = `paths.canonical_url(f"p/{slug}.html")`).

- [ ] **Step 1: Modify `build_one`** to pass canonical (path unchanged). In `build.py:44`, change the render call:

```python
    html = render.render_profile(
        faculty, photo_ref=photo_ref,
        asset_root="../", canonical=paths.canonical_url(f"p/{slug}.html"))
```

- [ ] **Step 2: Rewrite `build_dept`** (`build.py:50-70`) to nest under the college and thread depth-2 asset_root + canonical:

```python
def build_dept(org: dict, out_root: str, college_seg: str, photo_map: dict = None) -> str:
    """Render one department's leaderboard at <college>/<dept>/index.html."""
    roster = rank.roster(org["node_id"])
    coverage = rank.coverage(org["node_id"])
    views = {"rank": rank.by_rank(roster), "citations": rank.by_citations(roster),
             "az": rank.by_name(roster)}
    rising = rank.rising(roster)
    stats = rank.leaderboard_stats(roster, coverage)
    assets_dir = paths.assets_dir(out_root)
    photo_map = dict(photo_map or {})
    for r in roster:
        if r["slug"] not in photo_map:
            photo_map[r["slug"]] = _resolve_photo(r["slug"], db.get_faculty(r["slug"]), assets_dir)
    canonical = paths.canonical_url(f"{college_seg}/{org['slug']}/")
    html = render.render_leaderboard(org["name"], views, stats, coverage, photo_map,
                                     rising=rising, asset_root="../../", canonical=canonical)
    path = paths.leaderboard_path(out_root, college_seg, org["slug"])
    _write(path, html)
    return path
```

- [ ] **Step 3: Add `build_college_hub`** (cards per dept; dept-less college → its own leaderboard per D8):

```python
def build_college_hub(college_node: int, college_seg: str, out_root: str) -> str:
    """College hub at <college>/index.html: a card per dept/school with faculty>0."""
    depts = db.dept_orgs_of_college(college_node)
    cards = []
    for d in depts:
        n, m = rank.coverage(d["node_id"])
        cards.append({"name": d["name"], "faculty": m, "scholar": n,
                      "url": f"{d['slug']}/index.html"})
    canonical = paths.canonical_url(f"{college_seg}/")
    html = render.render_hub(db.college_name(college_node), cards, eyebrow="College",
                             asset_root="../", canonical=canonical)
    path = paths.college_hub_path(out_root, college_seg)
    _write(path, html)
    return path
```

- [ ] **Step 4: Add `build_njit_hub`** (cards per published college, subtree-distinct counts):

```python
def build_njit_hub(out_root: str) -> str:
    """NJIT hub at /index.html: a card per PUBLISHED college (subtree-distinct coverage)."""
    cards = []
    for slug in config.PUBLISHED_COLLEGES:            # registry order (deterministic)
        node = db.org_node_by_slug(slug)
        n, m = db.college_coverage(node)
        cards.append({"name": db.college_name(node), "faculty": m, "scholar": n,
                      "url": f"{slug}/index.html"})
    canonical = paths.canonical_url("")
    html = render.render_hub("New Jersey Institute of Technology", cards, eyebrow="University",
                             asset_root="", canonical=canonical)
    path = paths.njit_hub_path(out_root)
    _write(path, html)
    return path
```

- [ ] **Step 5: Add the `seo` + `paths` imports** at the top of build.py:

Change `from . import assets, config, db, paths, rank, render` to also import `seo`:
```python
from . import assets, config, db, paths, rank, render, seo
```

- [ ] **Step 6: Run existing tests to confirm no breakage of unchanged units**

Run: `python3 -m pytest facultyfolio/tests/ -q -k "not build"`
Expected: PASS (render/paths/db/seo). `build`-level tests are rewritten in Task 7/9.

- [ ] **Step 7: Commit**

```bash
git add facultyfolio/build.py
git commit -m "feat(facultyfolio): nested build_dept + college/NJIT hub builders + canonical threading"
```

---

### Task 7: build.py — scope-aware orchestrator, ancestor refresh, redirect-guard fix

**Files:**
- Modify: `facultyfolio/build.py`
- Test: `facultyfolio/tests/test_build_scoped.py` (create — full coverage in Task 9; a smoke test here)

**Interfaces:**
- Consumes: everything from Task 6.
- Produces:
  - `build_site(scope=None, out_root=None) -> dict` — the scope-aware orchestrator. `scope` is `None` (all published), `{"college": slug}`, or `{"dept": slug}`.
  - `_college_of_dept(dept_slug) -> str` — parent college slug via KG `part_of` (errors if unpublished).
  - `_emit_redirects(out_root, occupied_paths)` — writes legacy stubs only where the target root path is free.
  - `_emit_seo(out_root)` — writes sitemap.xml (full published set) + robots.txt.
  - `build_all(out_root=None)` retained as `build_site(scope=None, out_root=out_root)` for existing callers.

- [ ] **Step 1: Write the failing smoke test**

```python
# facultyfolio/tests/test_build_scoped.py
import os, tempfile
from facultyfolio import build

def test_full_build_writes_njit_hub_college_hub_and_nested_dept():
    with tempfile.TemporaryDirectory() as out:
        build.build_site(scope=None, out_root=out)
        assert os.path.exists(os.path.join(out, "index.html"))                       # NJIT hub
        assert os.path.exists(os.path.join(out, "ywcc", "index.html"))               # college hub
        assert os.path.exists(os.path.join(out, "ywcc", "computer-science", "index.html"))
        assert os.path.exists(os.path.join(out, "p", "koutis.html"))                 # profile flat
        assert os.path.exists(os.path.join(out, "sitemap.xml"))
        assert os.path.exists(os.path.join(out, "robots.txt"))
        # legacy redirect written at a now-free root segment
        assert os.path.exists(os.path.join(out, "computer-science", "index.html"))

def test_scoped_dept_build_writes_ancestors_not_siblings():
    with tempfile.TemporaryDirectory() as out:
        build.build_site(scope=None, out_root=out)          # full first
        sib = os.path.join(out, "ywcc", "data-science", "index.html")
        with open(sib, "w") as fh: fh.write("SENTINEL")     # tamper a sibling
        build.build_site(scope={"dept": "computer-science"}, out_root=out)
        assert os.path.exists(os.path.join(out, "ywcc", "computer-science", "index.html"))
        assert os.path.exists(os.path.join(out, "index.html"))         # ancestor refreshed
        with open(sib) as fh:
            assert fh.read() == "SENTINEL"                  # sibling untouched
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest facultyfolio/tests/test_build_scoped.py -q`
Expected: FAIL (`build.build_site` missing).

- [ ] **Step 3: Replace `build_all` with the scope-aware orchestrator** (`build.py:99-131`):

```python
def _college_of_dept(dept_slug: str) -> str:
    """Parent college slug of a dept, via KG part_of. Raises if dept/college unknown or unpublished."""
    node = db.org_node_by_slug(dept_slug)
    if node is None:
        raise ValueError(f"unknown dept slug {dept_slug!r}")
    for slug in config.PUBLISHED_COLLEGES:
        college_node = db.org_node_by_slug(slug)
        if any(d["slug"] == dept_slug for d in db.dept_orgs_of_college(college_node)):
            return slug
    raise ValueError(f"dept {dept_slug!r} is not under any published college")


def _build_dept_scope(college_seg, org, out_root, built, photo_map):
    """Build one dept's profiles + leaderboard into the shared maps."""
    assets_dir = paths.assets_dir(out_root)
    for s in db.faculty_slugs(org["node_id"]):
        if s in built:
            print(f"[facultyfolio] WARN dup-home faculty {s!r}: kept under {built[s]!r}, "
                  f"skipped under {org['slug']!r}")
            continue
        built[s] = org["slug"]
        faculty = db.get_faculty(s)
        if faculty["suppressed"]:
            continue
        ref = _resolve_photo(s, faculty, assets_dir)
        photo_map[s] = ref
        build_one(s, out_root, photo_ref=ref)
    build_dept(org, out_root, college_seg, photo_map=photo_map)


def build_site(scope: dict = None, out_root: str = None) -> dict:
    """Scope-aware build. scope=None -> all published; {'college': s}; {'dept': s}.
    Always regenerates the NJIT hub + affected college hub(s) + SEO (ancestor consistency)."""
    out_root = out_root or config.OUT_ROOT
    built, photo_map = {}, {}

    if scope and "dept" in scope:
        college_slugs = [_college_of_dept(scope["dept"])]
        dept_filter = scope["dept"]
    elif scope and "college" in scope:
        if scope["college"] not in config.PUBLISHED_COLLEGES:
            raise ValueError(f"college {scope['college']!r} is not published")
        college_slugs = [scope["college"]]
        dept_filter = None
    else:
        college_slugs = list(config.PUBLISHED_COLLEGES)
        dept_filter = None

    for cslug in college_slugs:
        cnode = db.org_node_by_slug(cslug)
        for org in db.dept_orgs_of_college(cnode):
            if dept_filter and org["slug"] != dept_filter:
                continue
            _build_dept_scope(cslug, org, out_root, built, photo_map)
        build_college_hub(cnode, cslug, out_root)

    build_njit_hub(out_root)                 # ancestor: always refreshed
    occupied = _occupied_root_segments(out_root)
    _emit_redirects(out_root, occupied)
    _emit_seo(out_root)
    assets.copy_assets(out_root)
    return {"built": sorted(built), "count": len(built)}


def build_all(out_root: str = None) -> dict:   # back-compat alias
    return build_site(scope=None, out_root=out_root)
```

- [ ] **Step 4: Add the redirect + SEO + occupancy helpers**

```python
def _occupied_root_segments(out_root: str) -> set:
    """Root-level segment names that already hold a real page (published college hubs +
    any existing root dir). Used so a legacy stub never clobbers a real page (C-1)."""
    occupied = set(config.PUBLISHED_COLLEGES)          # e.g. 'ywcc' hub occupies /ywcc/
    return occupied


def _emit_redirects(out_root: str, occupied: set) -> None:
    for old, target in config.LEGACY_REDIRECTS.items():
        if old in occupied:                            # never clobber a real root page (C-1)
            continue
        _write(paths.redirect_path(out_root, old), _redirect_html(target))


def _all_published_urls(out_root: str) -> list:
    """Every canonical URL in the published site, for the sitemap (full set, even on a scoped build)."""
    urls = [paths.canonical_url("")]                   # NJIT hub
    for cslug in config.PUBLISHED_COLLEGES:
        cnode = db.org_node_by_slug(cslug)
        urls.append(paths.canonical_url(f"{cslug}/"))  # college hub
        for org in db.dept_orgs_of_college(cnode):
            urls.append(paths.canonical_url(f"{cslug}/{org['slug']}/"))
            for slug in db.faculty_slugs(org["node_id"]):
                urls.append(paths.canonical_url(f"p/{slug}.html"))
    # de-dup (dup-home faculty appear once) preserving order
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u); out.append(u)
    return out


def _emit_seo(out_root: str) -> None:
    _write(paths.sitemap_path(out_root), seo.sitemap_xml(_all_published_urls(out_root)))
    _write(paths.robots_path(out_root), seo.robots_txt())
```

Note: `_redirect_html` already composes `../{target}/index.html`; a two-segment `target` like
`ywcc/computer-science` resolves correctly relative to `/{old}/index.html`. Update its canonical line
to absolute:

```python
def _redirect_html(target_segment: str) -> str:
    rel = f"../{target_segment}/index.html"
    canon = f"{config.SITE_ORIGIN}/{target_segment}/"
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta http-equiv="refresh" content="0; url={rel}">\n'
        f'<link rel="canonical" href="{canon}">\n'
        '<title>FacultyFolio</title></head>\n'
        f'<body><p>Redirecting to the <a href="{rel}">FacultyFolio faculty directory</a>…</p></body></html>\n'
    )
```

- [ ] **Step 5: Update `main()`** (`build.py:134`) to keep the CLI working (argparse added in Task 8; for now):

```python
def main():
    result = build_all()
    print(f"FacultyFolio: {result['count']} faculty across {len(config.PUBLISHED_COLLEGES)} "
          f"published college(s) -> {config.OUT_ROOT}")
```

- [ ] **Step 6: Run the smoke test + full suite**

Run: `python3 -m pytest facultyfolio/tests/test_build_scoped.py facultyfolio/tests/ -q`
Expected: PASS. Fix any existing `build_all`/`build_dept` caller/test that broke (e.g. a test calling `build_dept(org, out)` with the old 2-arg signature → update to pass `college_seg="ywcc"`).

- [ ] **Step 7: Commit**

```bash
git add facultyfolio/build.py facultyfolio/tests/test_build_scoped.py
git commit -m "feat(facultyfolio): scope-aware orchestrator + ancestor refresh + redirect-guard fix + SEO emit"
```

---

### Task 8: CLI — argparse --college / --dept / default all

**Files:**
- Modify: `facultyfolio/build.py`
- Test: `facultyfolio/tests/test_build_scoped.py` (extend)

**Interfaces:**
- Consumes: `build_site`.
- Produces: `python -m facultyfolio.build [--college SLUG | --dept SLUG]` → calls `build_site` with the matching scope; mutually exclusive; default = all published.

- [ ] **Step 1: Write the failing test**

```python
# append to facultyfolio/tests/test_build_scoped.py
from facultyfolio import build as _b

def test_parse_scope_from_args():
    assert _b._scope_from_args([]) is None
    assert _b._scope_from_args(["--college", "ywcc"]) == {"college": "ywcc"}
    assert _b._scope_from_args(["--dept", "computer-science"]) == {"dept": "computer-science"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest facultyfolio/tests/test_build_scoped.py::test_parse_scope_from_args -q`
Expected: FAIL (`_scope_from_args` missing).

- [ ] **Step 3: Add argparse to build.py**

```python
def _scope_from_args(argv):
    import argparse
    p = argparse.ArgumentParser(prog="facultyfolio.build")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--college", metavar="SLUG")
    g.add_argument("--dept", metavar="SLUG")
    a = p.parse_args(argv)
    if a.college:
        return {"college": a.college}
    if a.dept:
        return {"dept": a.dept}
    return None


def main(argv=None):
    import sys
    scope = _scope_from_args(sys.argv[1:] if argv is None else argv)
    result = build_site(scope=scope)
    label = "all published" if scope is None else next(iter(scope.items()))
    print(f"FacultyFolio: built {result['count']} faculty ({label}) -> {config.OUT_ROOT}")
```

- [ ] **Step 4: Run test + a live scoped invocation**

Run: `python3 -m pytest facultyfolio/tests/test_build_scoped.py -q`
Expected: PASS.
Run: `python3 -m facultyfolio.build --dept computer-science` (writes to `config.OUT_ROOT`; safe — additive). Expected: prints `built N faculty (('dept', 'computer-science'))`.

- [ ] **Step 5: Commit**

```bash
git add facultyfolio/build.py facultyfolio/tests/test_build_scoped.py
git commit -m "feat(facultyfolio): scoping CLI (--college/--dept, default all)"
```

---

### Task 9: Integration tests — scoped safety, counts, sitemap, uniqueness, byte-stability

**Files:**
- Modify: `facultyfolio/tests/test_build_scoped.py`

**Interfaces:**
- Consumes: `build.build_site`, `db.college_coverage`.

- [ ] **Step 1: Write the tests**

```python
# append to facultyfolio/tests/test_build_scoped.py
import os, tempfile, re
from facultyfolio import build, db, config, paths

def _read(p):
    with open(p) as fh: return fh.read()

def test_manifest_of_scoped_dept_build():
    """A --dept build writes exactly: CS profiles + CS leaderboard + ancestors + SEO. No DS/Info pages."""
    with tempfile.TemporaryDirectory() as out:
        build.build_site(scope={"dept": "computer-science"}, out_root=out)
        assert os.path.exists(os.path.join(out, "ywcc", "computer-science", "index.html"))
        assert os.path.exists(os.path.join(out, "index.html"))
        assert os.path.exists(os.path.join(out, "ywcc", "index.html"))
        assert not os.path.exists(os.path.join(out, "ywcc", "data-science", "index.html"))
        assert not os.path.exists(os.path.join(out, "ywcc", "informatics", "index.html"))

def test_njit_hub_count_matches_college_coverage():
    with tempfile.TemporaryDirectory() as out:
        build.build_site(scope=None, out_root=out)
        html = _read(os.path.join(out, "index.html"))
        _, m = db.college_coverage(db.org_node_by_slug("ywcc"))
        assert re.search(rf"<strong>{m}</strong>\s*faculty", html)

def test_scoped_sitemap_still_lists_out_of_scope_depts():
    with tempfile.TemporaryDirectory() as out:
        build.build_site(scope={"dept": "computer-science"}, out_root=out)
        sm = _read(os.path.join(out, "sitemap.xml"))
        assert f"{config.SITE_ORIGIN}/ywcc/data-science/" in sm       # out of build scope, in sitemap
        assert f"{config.SITE_ORIGIN}/ywcc/informatics/" in sm

def test_all_urls_absolute_in_sitemap():
    with tempfile.TemporaryDirectory() as out:
        build.build_site(scope=None, out_root=out)
        sm = _read(os.path.join(out, "sitemap.xml"))
        for loc in re.findall(r"<loc>(.*?)</loc>", sm):
            assert loc.startswith(config.SITE_ORIGIN + "/")

def test_full_build_is_byte_stable():
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        build.build_site(scope=None, out_root=a)
        build.build_site(scope=None, out_root=b)
        fa = _read(os.path.join(a, "ywcc", "computer-science", "index.html"))
        fb = _read(os.path.join(b, "ywcc", "computer-science", "index.html"))
        assert fa == fb
```

- [ ] **Step 2: Run the full suite**

Run: `python3 -m pytest facultyfolio/tests/ -q`
Expected: PASS (all tasks' tests + the pre-existing ~132).

- [ ] **Step 3: Commit**

```bash
git add facultyfolio/tests/test_build_scoped.py
git commit -m "test(facultyfolio): scoped-write safety, counts, sitemap, byte-stability integration"
```

---

## Post-plan verification (before the diff goes to Fable)

- [ ] Full suite green: `python3 -m pytest facultyfolio/tests/ -q`.
- [ ] Live full build to a scratch dir and eyeball the tree shape:
  `FACULTYFOLIO_OUT=/tmp/ff_preview python3 -m facultyfolio.build && find /tmp/ff_preview -maxdepth 2 -name index.html`.
- [ ] Serve `/tmp/ff_preview` and click: NJIT hub → YWCC → CS leaderboard → a profile; check assets load (asset_root depth) and the legacy `/computer-science/` redirect lands on `/ywcc/computer-science/`.
- [ ] Fill the §13 goals checklist in the spec (shipped/deferred) — per the review-against-plan rule.
- [ ] Diff to Fable for review; then STOP — the merge to main + deploy to the Pages repo is the owner's gate.

## Self-review notes (author)

- **Spec coverage:** G1 (Tasks 2,4,6,7) · G2 (Task 1,7) · G3 (Task 8) · G4 (Task 7,9) · G5 (Tasks 1,5,7 + Task 4 canonical) · G6 (Task 9 byte-stability + the whole YWCC build). C-1 redirect guard (Task 7). S-2 college_coverage (Task 3). SITE_ORIGIN (Task 1). No spec requirement left unassigned.
- **Type consistency:** `build_dept` gains `college_seg` (Tasks 6,7 agree); `render_hub(title, cards, *, eyebrow, asset_root, canonical)` used identically in Tasks 4 and 6; `college_coverage -> (n, m)` consumed as `(n, m)` in Task 6/9.
- **Known follow-ups (not this plan):** MTSM dept-less hub shape + HCAD school label (spec §14); stale-file `--prune`; the photo gray-avatar fingerprint fix (separate TODO).
