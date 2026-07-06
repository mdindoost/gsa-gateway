# FacultyFolio — YWCC Departments + Hub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate FacultyFolio pages for the two remaining YWCC departments (Data Science, Informatics) plus a simple YWCC college hub, by parameterizing the six CS-bound entry points off an org node id — all DB-derived, no per-dept vocabulary.

**Architecture:** Keep the existing layered design (db → rank → format → render → build, with paths.py as output-path SSOT). Lift each CS hardcode to an org-node-id parameter; discover YWCC's departments from the KG at build time; add one new hub page type. The CS pilot's output is unchanged except three intended diffs (URL segment `cs/`→`computer-science/`, profile back-link, sources label).

**Tech Stack:** Python 3.11, Jinja2, read-only SQLite (`mode=ro`), pytest.

## Global Constraints

- **Nothing hardcoded per-dept/per-college; everything DB-derived** (owner hard rule). URL segments come from `organizations.slug`. The only permitted maps: `config.COLLEGE_NAMES` (full college name — absent from the DB) and `config.LEGACY_REDIRECTS` (old→new URL continuity). Both documented as such.
- **`format.py` stays mechanical-only** — no lookup tables (base spec §3.4). This plan does not touch format.py.
- **db.py is the ONLY module that opens SQLite**, always `mode=ro`.
- **YWCC college is anchored by SLUG (`'ywcc'`), never a bare node id** — node ids renumber on `run_explore.py --reset`; slugs are stable.
- **Determinism:** dept discovery is `ORDER BY organizations.slug`; every build output is byte-identical on rebuild (guarded by `test_idempotent`).
- **Cross-dept profile dedup is LOUD** — print a warning naming the slug + both depts, keep the first in sorted build order.
- **Batch rebuild:** the actual site rebuild + deploy happens ONCE at the end (Task 7), owner-gated. Do not deploy mid-plan.
- Verified KG anchors (live DB, 2026-07-06): YWCC college = node 299 / slug `ywcc`. Depts: `computer-science`(16, 57), `data-science`(73, 21), `informatics`(100, 41), `college-administration`(2, 0 → excluded). CS_ORG_ID=16 stays as a named convenience anchor (tests reference it); build no longer depends on it.

---

### Task 1: db layer — org-parametric slugs, org metadata, dept discovery

**Files:**
- Modify: `facultyfolio/db.py`
- Test: `facultyfolio/tests/test_db.py`

**Interfaces:**
- Produces:
  - `db.faculty_slugs(org_id: int) -> list[str]` — home-faculty slugs for an org, `ORDER BY n.name`, minus `SUPPRESSED`.
  - `db.cs_faculty_slugs() -> list[str]` — now a thin alias: `return faculty_slugs(config.CS_ORG_ID)`.
  - `db.org_node_by_slug(slug: str) -> int | None` — the `nodes.id` of the Org whose `organizations.slug == slug`.
  - `db.dept_orgs_of_college(college_node_id: int) -> list[dict]` — `[{"node_id","slug","name","faculty"}]` for each `part_of` child Org with faculty>0, sorted by slug.
  - `db.college_name(college_node_id: int) -> str` — the Org's name, expanded via `config.COLLEGE_NAMES`.
  - `db.get_faculty(...)` dict gains `"home_dept_segment"` — the home dept's org slug (or `None`).

- [ ] **Step 1: Write the failing tests**

Add to `facultyfolio/tests/test_db.py`:

```python
def test_faculty_slugs_per_org():
    assert len(db.faculty_slugs(16)) == 57                    # Computer Science
    assert len(db.faculty_slugs(73)) == 21                    # Data Science
    assert len(db.faculty_slugs(100)) == 41                   # Informatics
    # cs_faculty_slugs is now an alias for the CS org
    assert db.faculty_slugs(config.CS_ORG_ID) == db.cs_faculty_slugs()


def test_org_node_by_slug():
    assert db.org_node_by_slug("ywcc") == 299
    assert db.org_node_by_slug("computer-science") == 16
    assert db.org_node_by_slug("no-such-org") is None


def test_dept_orgs_of_college_discovers_ywcc_depts():
    depts = db.dept_orgs_of_college(299)
    slugs = [d["slug"] for d in depts]
    # only faculty>0 depts, sorted by slug; College Administration (0 faculty) excluded
    assert slugs == ["computer-science", "data-science", "informatics"]
    assert "college-administration" not in slugs
    by_slug = {d["slug"]: d for d in depts}
    assert by_slug["data-science"]["faculty"] == 21
    assert by_slug["data-science"]["node_id"] == 73
    assert by_slug["computer-science"]["name"] == "Computer Science"


def test_college_name_expands_acronym():
    assert db.college_name(299) == "Ying Wu College of Computing"


def test_get_faculty_home_dept_segment():
    assert db.get_faculty(33)["home_dept_segment"] == "computer-science"   # Koutis
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest facultyfolio/tests/test_db.py -q`
Expected: FAIL (`AttributeError: module 'facultyfolio.db' has no attribute 'faculty_slugs'`, etc.)

- [ ] **Step 3: Implement**

In `facultyfolio/db.py`, add a private slug helper near `_org_name` (after line ~40):

```python
def _org_slug(conn, node_id):
    """The organizations.slug for an Org node (via nodes.attrs.org_id). None if absent."""
    r = conn.execute("SELECT attrs FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not r or not r["attrs"]:
        return None
    oid = json.loads(r["attrs"]).get("org_id")
    if oid is None:
        return None
    s = conn.execute("SELECT slug FROM organizations WHERE id=?", (oid,)).fetchone()
    return s["slug"] if s else None
```

In `get_faculty`, in the roles loop where `e["category"] == "faculty"` (currently sets `home_dept`, `title`, `college`), also capture the segment:

```python
            if e["category"] == "faculty":
                home_dept = _org_name(conn, e["dst_id"])
                title = ", ".join(titles) if titles else None
                college = _college_of(conn, e["dst_id"])
                home_dept_segment = _org_slug(conn, e["dst_id"])
```

Initialize `home_dept_segment = None` alongside `home_dept = joint_dept = title = None` (line ~76), and add `"home_dept_segment": home_dept_segment,` to the returned dict (next to `"college": college,`).

Replace `cs_faculty_slugs` (lines 153-169) with the parametric version + alias, and add the three new public functions:

```python
def faculty_slugs(org_id) -> list:
    """Slugs of an org's home faculty (has_role category='faculty' -> org), minus suppressed."""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT n.key AS key FROM nodes n
               JOIN edges e ON e.src_id=n.id
               WHERE n.type='Person' AND n.is_active=1
                 AND e.type='has_role' AND e.category='faculty'
                 AND e.dst_id=? AND e.is_active=1
               ORDER BY n.name""",
            (org_id,),
        ).fetchall()
    finally:
        conn.close()
    slugs = [r["key"].split("/")[-1] for r in rows]
    return [s for s in slugs if s not in config.SUPPRESSED]


def cs_faculty_slugs() -> list:
    """CS home-faculty slugs. Thin alias over faculty_slugs (kept for existing callers/tests)."""
    return faculty_slugs(config.CS_ORG_ID)


def org_node_by_slug(slug):
    """The nodes.id of the Org whose organizations.slug == slug (None if not found)."""
    conn = connect()
    try:
        row = conn.execute(
            """SELECT n.id AS id FROM nodes n
               JOIN organizations o ON o.id = json_extract(n.attrs, '$.org_id')
               WHERE n.type='Org' AND o.slug=? LIMIT 1""",
            (slug,),
        ).fetchone()
    finally:
        conn.close()
    return row["id"] if row else None


def dept_orgs_of_college(college_node_id) -> list:
    """Department child Orgs of a college (part_of), with faculty>0, sorted by slug."""
    conn = connect()
    try:
        child_ids = [r["id"] for r in conn.execute(
            """SELECT n.id AS id FROM nodes n
               JOIN edges e ON e.src_id=n.id
               WHERE e.type='part_of' AND e.dst_id=? AND e.is_active=1
                 AND n.type='Org' AND n.is_active=1""",
            (college_node_id,),
        ).fetchall()]
        out = []
        for nid in child_ids:
            fac = conn.execute(
                """SELECT COUNT(DISTINCT n2.id) FROM nodes n2
                   JOIN edges e2 ON e2.src_id=n2.id
                   WHERE e2.type='has_role' AND e2.category='faculty'
                     AND e2.dst_id=? AND e2.is_active=1 AND n2.is_active=1""",
                (nid,),
            ).fetchone()[0]
            if fac > 0:
                out.append({"node_id": nid, "slug": _org_slug(conn, nid),
                            "name": _org_name(conn, nid), "faculty": fac})
    finally:
        conn.close()
    out.sort(key=lambda d: d["slug"] or "")
    return out


def college_name(college_node_id) -> str:
    """Org node name, expanded via config.COLLEGE_NAMES (acronym -> full college name)."""
    conn = connect()
    try:
        short = _org_name(conn, college_node_id) or ""
    finally:
        conn.close()
    return config.COLLEGE_NAMES.get(short, short)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest facultyfolio/tests/test_db.py -q`
Expected: PASS (including the pre-existing `test_cs_faculty_slugs` — the alias keeps it green).

- [ ] **Step 5: Commit**

```bash
git add facultyfolio/db.py facultyfolio/tests/test_db.py
git commit -m "feat(facultyfolio): org-parametric db layer (faculty_slugs, dept discovery, home segment)"
```

---

### Task 2: rank.roster honors its org_id argument (fixes latent bug)

**Files:**
- Modify: `facultyfolio/rank.py:38-61`
- Test: `facultyfolio/tests/test_rank.py`

**Interfaces:**
- Consumes: `db.faculty_slugs(org_id)` (Task 1).
- Produces: `rank.roster(org_id)` now enumerates the given org (was always CS).

- [ ] **Step 1: Write the failing test**

Add to `facultyfolio/tests/test_rank.py`:

```python
def test_roster_honors_org_id():
    cs = rank.roster(16)
    ds = rank.roster(73)
    assert len(cs) == 57 and len(ds) == 21       # each org, not always CS
    assert {r["slug"] for r in cs}.isdisjoint({r["slug"] for r in ds})   # home rosters disjoint
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest facultyfolio/tests/test_rank.py::test_roster_honors_org_id -q`
Expected: FAIL (`ds` has 57 rows — arg ignored, returns CS).

- [ ] **Step 3: Implement**

In `facultyfolio/rank.py`, change `roster` line 47 from `for slug in db.cs_faculty_slugs():` to:

```python
    for slug in db.faculty_slugs(org_id):
```

Update the docstring lines 44-45 (drop "Scope is CS for now"):

```python
    all views can show the full department. Title comes from `get_faculty` so it
    matches the person's profile page verbatim; rank_index/label from `rank_of`.
    Enumerates the org given by `org_id` (home faculty of that dept).
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest facultyfolio/tests/test_rank.py -q`
Expected: PASS (all — the CS-arg tests still pass since 16 is CS).

- [ ] **Step 5: Commit**

```bash
git add facultyfolio/rank.py facultyfolio/tests/test_rank.py
git commit -m "fix(facultyfolio): rank.roster honors its org_id arg (was hardcoded to CS)"
```

---

### Task 3: paths — parameterized leaderboard segment, hub, redirect

**Files:**
- Modify: `facultyfolio/paths.py`
- Test: `facultyfolio/tests/test_flags.py:37-41`

**Interfaces:**
- Produces:
  - `paths.leaderboard_path(out_root, segment) -> str` — `<out_root>/<segment>/index.html`.
  - `paths.hub_path(out_root) -> str` — `<out_root>/index.html`.
  - `paths.redirect_path(out_root, old_segment) -> str` — `<out_root>/<old_segment>/index.html`.

- [ ] **Step 1: Update the failing test**

In `facultyfolio/tests/test_flags.py`, replace the leaderboard assertion in `test_paths_ssot_matches_current_layout` (line 40) and add hub/redirect checks:

```python
    assert paths.profile_path("/out", "koutis") == "/out/p/koutis.html"
    assert paths.leaderboard_path("/out", "computer-science") == "/out/computer-science/index.html"
    assert paths.hub_path("/out") == "/out/index.html"
    assert paths.redirect_path("/out", "cs") == "/out/cs/index.html"
    assert paths.assets_dir("/out") == "/out/assets"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest facultyfolio/tests/test_flags.py::test_paths_ssot_matches_current_layout -q`
Expected: FAIL (`leaderboard_path()` takes 1 arg / no `hub_path`).

- [ ] **Step 3: Implement**

Replace `leaderboard_path` in `facultyfolio/paths.py` and add the two new functions:

```python
def leaderboard_path(out_root: str, segment: str) -> str:
    return os.path.join(out_root, segment, "index.html")


def hub_path(out_root: str) -> str:
    return os.path.join(out_root, "index.html")


def redirect_path(out_root: str, old_segment: str) -> str:
    return os.path.join(out_root, old_segment, "index.html")
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest facultyfolio/tests/test_flags.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add facultyfolio/paths.py facultyfolio/tests/test_flags.py
git commit -m "feat(facultyfolio): parameterize leaderboard segment + add hub/redirect paths"
```

---

### Task 4: render + templates — generalized sources label, home-dept back-link, asset_root

**Files:**
- Modify: `facultyfolio/render.py:20`, `:175`, `:150-179`
- Modify: `facultyfolio/templates/base.html:7`
- Modify: `facultyfolio/templates/profile.html:40`
- Test: `facultyfolio/tests/test_render.py`

**Interfaces:**
- Consumes: `db.get_faculty(...)["home_dept_segment"]` (Task 1).
- Produces: profile ctx gains `home_dept_segment`; `sources` no longer says `-CS`; a Jinja global `asset_root="../"` (overridable per-render) so a root-level page can point at `assets/`.

- [ ] **Step 1: Write the failing tests**

Add to `facultyfolio/tests/test_render.py`:

```python
def test_profile_sources_label_not_cs_specific():
    html = render.render_profile(db.get_faculty(33))            # Koutis has Scholar
    assert "Scholar + NJIT" in html and "NJIT-CS" not in html


def test_profile_back_link_uses_home_segment():
    html = render.render_profile(db.get_faculty(33))            # home = Computer Science
    assert '../computer-science/index.html' in html
    assert '../cs/index.html' not in html
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest facultyfolio/tests/test_render.py::test_profile_sources_label_not_cs_specific facultyfolio/tests/test_render.py::test_profile_back_link_uses_home_segment -q`
Expected: FAIL (`NJIT-CS` present; `../cs/index.html` present).

- [ ] **Step 3: Implement**

In `facultyfolio/render.py`, register the `asset_root` global after line 20:

```python
_env.globals["assistant_version"] = config.ASSISTANT_VERSION
# Depth prefix from a page to the assets dir. Profiles/leaderboards live one level
# deep (p/, <dept>/) -> "../"; the root hub overrides to "" (Task 5).
_env.globals["asset_root"] = "../"
```

In `render_profile`, change the sources line (175) and add the segment to the ctx:

```python
        "sources": "Scholar + NJIT" if sch else "NJIT",
        "home_dept_segment": f.get("home_dept_segment") or "",
```

In `facultyfolio/templates/base.html` line 7, make the stylesheet href depth-relative:

```html
<link rel="stylesheet" href="{{ asset_root }}assets/style.css">
```

In `facultyfolio/templates/profile.html` line 40, use the home-dept segment (guarded):

```html
        {% if home_dept_segment %}<a href="../{{ home_dept_segment }}/index.html">{{ home_dept }}</a>{% else %}{{ home_dept }}{% endif %}
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest facultyfolio/tests/test_render.py -q`
Expected: PASS (existing render tests unaffected — `asset_root` default `"../"` keeps `../assets/style.css`).

- [ ] **Step 5: Commit**

```bash
git add facultyfolio/render.py facultyfolio/templates/base.html facultyfolio/templates/profile.html facultyfolio/tests/test_render.py
git commit -m "feat(facultyfolio): generalize sources label + home-dept back-link; add asset_root"
```

---

### Task 5: hub page — template, CSS, render_hub

**Files:**
- Create: `facultyfolio/templates/hub.html`
- Modify: `facultyfolio/assets/style.css` (append hub-card rules)
- Modify: `facultyfolio/render.py` (add `render_hub`)
- Test: `facultyfolio/tests/test_render.py`

**Interfaces:**
- Consumes: `asset_root` global (Task 4).
- Produces: `render.render_hub(college_name: str, cards: list[dict]) -> str`. Each card: `{"name","faculty","scholar","url"}`.

- [ ] **Step 1: Write the failing tests**

Add to `facultyfolio/tests/test_render.py`:

```python
def test_render_hub_cards_and_counts():
    cards = [
        {"name": "Computer Science", "faculty": 57, "scholar": 34, "url": "computer-science/index.html"},
        {"name": "Data Science", "faculty": 21, "scholar": 15, "url": "data-science/index.html"},
    ]
    html = render.render_hub("Ying Wu College of Computing", cards)
    assert "Ying Wu College of Computing" in html
    assert 'href="computer-science/index.html"' in html
    assert ">57<" in html and "on Google Scholar" in html
    assert 'href="assets/style.css"' in html          # root page -> no ../
    from facultyfolio import config
    assert config.ASSISTANT_VERSION in html            # shared footer


def test_render_hub_escapes_hostile_card():
    cards = [{"name": 'X <script>alert(1)</script>', "faculty": 1, "scholar": 0, "url": "x/index.html"}]
    html = render.render_hub("C & <b>", cards)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "<b>" not in html.split("footer")[0] or "&lt;b&gt;" in html
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest facultyfolio/tests/test_render.py::test_render_hub_cards_and_counts -q`
Expected: FAIL (`render` has no `render_hub`).

- [ ] **Step 3: Implement**

Create `facultyfolio/templates/hub.html`:

```html
{% extends "base.html" %}
{% block title %}{{ college_name }} — FacultyFolio{% endblock %}

{% block rail %}
  <div class="rail">
    <span><span class="dot"></span>Crawled from public sources</span>
    <a class="claim" href="#">Is this you? Claim your page →</a>
  </div>
{% endblock %}

{% block body %}
  <div class="lb">
    <section>
      <p class="eyebrow">College</p>
      <h2>{{ college_name }}</h2>
      <div class="rule"></div>
      <div class="hub-cards">
        {% for c in cards %}
        <a class="hub-card" href="{{ c.url }}">
          <span class="hub-name">{{ c.name }}</span>
          <span class="hub-stats"><strong>{{ c.faculty }}</strong> faculty · <strong>{{ c.scholar }}</strong> on Google Scholar</span>
          <span class="hub-go">View directory →</span>
        </a>
        {% endfor %}
      </div>
    </section>
  </div>
{% endblock %}
```

Append to `facultyfolio/assets/style.css`:

```css
/* --- college hub cards --- */
.hub-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 1rem; margin-top: 1.4rem; }
.hub-card { display: flex; flex-direction: column; gap: .5rem; padding: 1.2rem 1.3rem; border: 1px solid var(--hair); border-radius: 14px; text-decoration: none; color: inherit; transition: border-color .15s, transform .15s; }
.hub-card:hover { border-color: var(--mute); transform: translateY(-2px); }
.hub-name { font-family: var(--f-display); font-size: 1.15rem; font-weight: 600; }
.hub-stats { color: var(--mute); font-size: .92rem; }
.hub-go { color: var(--accent, #b5472a); font-size: .9rem; margin-top: .2rem; }
```

Add `render_hub` to `facultyfolio/render.py` (after `render_leaderboard`):

```python
def render_hub(college_name: str, cards: list) -> str:
    """College hub landing page: a card per department (name, faculty count, Scholar count).
    Root-level page, so asset_root='' points at assets/ (not ../assets/)."""
    return _env.get_template("hub.html").render(
        college_name=college_name, cards=cards, asset_root="")
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest facultyfolio/tests/test_render.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add facultyfolio/templates/hub.html facultyfolio/assets/style.css facultyfolio/render.py facultyfolio/tests/test_render.py
git commit -m "feat(facultyfolio): YWCC college hub page (render_hub + template + card CSS)"
```

---

### Task 6: build orchestration — multi-dept build_all, hub, legacy redirect

**Files:**
- Modify: `facultyfolio/config.py` (add `COLLEGE_SLUG`, `LEGACY_REDIRECTS`)
- Modify: `facultyfolio/build.py` (`build_dept`, `build_hub`, `_redirect_html`, `build_all`)
- Test: `facultyfolio/tests/test_build.py`

**Interfaces:**
- Consumes: `db.dept_orgs_of_college`, `db.org_node_by_slug`, `db.faculty_slugs`, `db.college_name` (Task 1); `rank.roster`/`coverage` (Task 2); `paths.leaderboard_path(out_root, segment)`, `paths.hub_path`, `paths.redirect_path` (Task 3); `render.render_hub`, `render.render_leaderboard` (Tasks 4-5).
- Produces: `build.build_dept(org, out_root, photo_map=None) -> str`; `build.build_hub(out_root, college_node, depts) -> str`; `build.build_all(out_root)` returns `{"profiles","leaderboards","hub","count"}`.

- [ ] **Step 1: Write/UPDATE the failing tests**

In `facultyfolio/tests/test_build.py`, replace `test_build_all_and_leaderboard` and `test_idempotent` (they assumed CS-only + `cs/`):

```python
def test_build_all_and_leaderboard(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "photos_ensure", lambda slug, *a, **k: f"monogram:{slug[:2].upper()}")
    from facultyfolio import db, config
    res = build.build_all(str(tmp_path))
    # a profile per YWCC home faculty across all three depts (57 + 21 + 41)
    depts = db.dept_orgs_of_college(db.org_node_by_slug(config.COLLEGE_SLUG))
    assert res["count"] == sum(len(db.faculty_slugs(d["node_id"])) for d in depts)
    # each dept leaderboard exists at its org slug; college-administration never gets one
    assert os.path.exists(os.path.join(tmp_path, "computer-science", "index.html"))
    assert os.path.exists(os.path.join(tmp_path, "data-science", "index.html"))
    assert os.path.exists(os.path.join(tmp_path, "informatics", "index.html"))
    assert not os.path.exists(os.path.join(tmp_path, "college-administration", "index.html"))
    # hub at root, profiles flat, legacy cs/ redirect preserved, assets copied
    assert os.path.exists(os.path.join(tmp_path, "index.html"))
    assert os.path.exists(os.path.join(tmp_path, "p", "ikoutis.html"))
    assert os.path.exists(os.path.join(tmp_path, "assets", "style.css"))
    cs_redirect = open(os.path.join(tmp_path, "cs", "index.html")).read()
    assert "url=../computer-science/index.html" in cs_redirect
    hub = open(os.path.join(tmp_path, "index.html")).read()
    assert "Ying Wu College of Computing" in hub


def test_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "photos_ensure", lambda slug, *a, **k: f"monogram:{slug[:2].upper()}")

    def digest():
        out = {}
        for root, _dirs, files in os.walk(tmp_path):
            for f in sorted(files):
                if f.endswith(".html"):
                    p = os.path.join(root, f)
                    out[os.path.relpath(p, tmp_path)] = hashlib.md5(open(p, "rb").read()).hexdigest()
        return out

    build.build_all(str(tmp_path))
    h1 = digest()
    build.build_all(str(tmp_path))
    h2 = digest()
    assert h1 == h2                    # byte-identical rebuild
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest facultyfolio/tests/test_build.py -q`
Expected: FAIL (`build_all` still writes `cs/`, count == 57, no hub).

- [ ] **Step 3: Implement**

In `facultyfolio/config.py`, after `CS_ORG_ID`/`KOUTIS_NODE` (line ~19) add:

```python
# YWCC college anchored by SLUG (node ids renumber on `run_explore.py --reset`; slugs don't).
COLLEGE_SLUG = "ywcc"
# Old URL segment -> new segment. Preserves a previously-shared URL after the slug move
# (URL-migration continuity, NOT per-dept vocabulary). A stub redirect is written per entry.
LEGACY_REDIRECTS = {"cs": "computer-science"}
```

In `facultyfolio/build.py`, replace `build_leaderboard` (lines 50-69) with `build_dept`, and add `build_hub` + `_redirect_html`:

```python
def build_dept(org: dict, out_root: str, photo_map: dict = None) -> str:
    """Render one department's 3-view leaderboard at <org slug>/index.html.

    `org` = {"node_id","slug","name","faculty"} from db.dept_orgs_of_college.
    Reuses the profile pass's photo refs when given; resolves any missing itself.
    """
    roster = rank.roster(org["node_id"])
    coverage = rank.coverage(org["node_id"])
    views = {"rank": rank.by_rank(roster), "citations": rank.by_citations(roster),
             "az": rank.by_name(roster)}
    stats = rank.leaderboard_stats(roster, coverage)
    assets_dir = paths.assets_dir(out_root)
    photo_map = dict(photo_map or {})
    for r in roster:
        if r["slug"] not in photo_map:
            photo_map[r["slug"]] = _resolve_photo(r["slug"], db.get_faculty(r["slug"]), assets_dir)
    html = render.render_leaderboard(org["name"], views, stats, coverage, photo_map)
    path = paths.leaderboard_path(out_root, org["slug"])
    _write(path, html)
    return path


def build_hub(out_root: str, college_node: int, depts: list) -> str:
    """Render the college hub at root index.html: a card per department."""
    cards = [
        {"name": org["name"], "faculty": rank.coverage(org["node_id"])[1],
         "scholar": rank.coverage(org["node_id"])[0], "url": f"{org['slug']}/index.html"}
        for org in depts
    ]
    html = render.render_hub(db.college_name(college_node), cards)
    path = paths.hub_path(out_root)
    _write(path, html)
    return path


def _redirect_html(target_segment: str) -> str:
    """A minimal meta-refresh page pointing from a legacy segment to the new one."""
    url = f"../{target_segment}/index.html"
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta http-equiv="refresh" content="0; url={url}">\n'
        f'<link rel="canonical" href="{url}">\n'
        '<title>FacultyFolio</title></head>\n'
        f'<body><p>Redirecting to the <a href="{url}">FacultyFolio faculty directory</a>…</p></body></html>\n'
    )
```

Replace `build_all` (lines 72-86) with the multi-dept version:

```python
def build_all(out_root: str = None) -> dict:
    out_root = out_root or config.OUT_ROOT
    assets_dir = paths.assets_dir(out_root)
    college_node = db.org_node_by_slug(config.COLLEGE_SLUG)
    depts = db.dept_orgs_of_college(college_node)      # sorted by slug, faculty>0

    photo_map, pages, built = {}, [], {}
    for org in depts:                                  # profiles: each unique home faculty once
        for s in db.faculty_slugs(org["node_id"]):
            if s in built:                             # dup-home (data regression) -> LOUD, keep first
                print(f"[facultyfolio] WARN dup-home faculty {s!r}: "
                      f"kept under {built[s]!r}, skipped under {org['slug']!r}")
                continue
            built[s] = org["slug"]
            faculty = db.get_faculty(s)
            if faculty["suppressed"]:
                continue
            ref = _resolve_photo(s, faculty, assets_dir)
            photo_map[s] = ref
            pages.append(build_one(s, out_root, photo_ref=ref))

    leaderboards = [build_dept(org, out_root, photo_map=photo_map) for org in depts]
    hub = build_hub(out_root, college_node, depts)
    for old, new in config.LEGACY_REDIRECTS.items():   # legacy URL continuity
        _write(paths.redirect_path(out_root, old), _redirect_html(new))
    assets.copy_assets(out_root)

    profiles = [p for p in pages if p]
    return {"profiles": profiles, "leaderboards": leaderboards, "hub": hub,
            "count": len(profiles)}
```

Update `main()` (lines 89-91) to the new shape:

```python
def main():
    result = build_all()
    print(f"FacultyFolio: {result['count']} profiles + "
          f"{len(result['leaderboards'])} dept leaderboards + hub -> {config.OUT_ROOT}")
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest facultyfolio/tests/test_build.py -q`
Expected: PASS

- [ ] **Step 5: Run the FULL suite (no regressions)**

Run: `python3 -m pytest facultyfolio/tests/ -q`
Expected: PASS (all — the CS-arg rank/db/render tests stay green).

- [ ] **Step 6: Commit**

```bash
git add facultyfolio/config.py facultyfolio/build.py facultyfolio/tests/test_build.py
git commit -m "feat(facultyfolio): multi-dept build_all + YWCC hub + legacy cs/ redirect"
```

---

### Task 7: senior-eng review of the diff, dry-run build, owner-gated rebuild + deploy

**Files:**
- No code. Verification + the single end-of-series rebuild/deploy.

- [ ] **Step 1: Full suite + a real dry-run build into a scratch dir**

Run:
```bash
python3 -m pytest facultyfolio/tests/ -q
FACULTYFOLIO_OUT=/tmp/claude-1001/-home-md724-gsa-gateway/c98b252e-c35f-4071-9633-80f5d280b8ed/scratchpad/ff_dryrun \
  python3 -m facultyfolio.build
```
Expected: suite PASS; the printed line shows `count == 119` (57+21+41, absent any dup-home WARN), `3 dept leaderboards + hub`. Inspect `/tmp/.../ff_dryrun`: root `index.html` is the hub (3 cards), `computer-science/ data-science/ informatics/ index.html` exist, `cs/index.html` is the redirect, `p/*.html` present, `college-administration/` absent. Note any `WARN dup-home` lines (Task 6 loud dedup) — if present, surface to the owner before deploying.

- [ ] **Step 2: Dispatch the senior-eng review of the full diff**

Dispatch a background general-purpose (or Fable) reviewer with: the spec path, the plan path, and `git diff main -- facultyfolio/`. Prompt it to (a) verify every spec goal shipped or loudly deferred (review-against-plan rule), (b) confirm no per-dept hardcoded vocabulary crept in, (c) confirm the CS pilot output changed only in the three intended ways (segment, back-link, sources label). Relay findings to the owner; fix anything real before deploy.

- [ ] **Step 3: OWNER GATE — show the diff, get sign-off**

Present `git diff main -- facultyfolio/` + the dry-run tree summary + the reviewer verdict. Do NOT proceed to deploy without the owner's explicit go (hard gate: show diff → sign off → deploy).

- [ ] **Step 4: Rebuild into the live Pages tree + deploy (owner-gated, ONCE)**

On sign-off:
```bash
python3 -m facultyfolio.build                       # writes to /home/md724/Faculty-Folio (config.OUT_ROOT)
cd /home/md724/Faculty-Folio
git add -A                                           # Pages repo has NO secrets — safe here (verify: git status)
git commit -m "YWCC: add Data Science + Informatics directories + college hub; CS -> computer-science/"
git push
```
Then watch the GitHub Actions "pages build and deployment" run to green (past deploys have transiently failed at the deploy step — re-run or push an empty commit if so). Verify live: `facultyfolio.github.io` (hub), `/data-science/`, `/informatics/`, `/computer-science/`, an old `/cs/` (redirects), and a couple of profile back-links.

- [ ] **Step 5: Checkpoint — memory + spec/plan goals checklist**

Tick the spec's goals checklist (shipped vs the loudly-deferred leadership section / full multi-college). Update `project_faculty_page_builder.md` + `MEMORY.md` with the YWCC-departments+hub outcome and the deferred items. Commit any doc updates.

---

## Self-Review

**Spec coverage:** A (entry-point parameterization) → Tasks 1-4,6; roster latent bug → Task 2; B (hub) → Task 5-6; C (URLs: root hub, org-slug segments, cs redirect) → Tasks 3,6; D (slug anchor, COLLEGE_NAMES justified, CS_ORG_ID kept-but-unused-by-build) → Tasks 1,6. Testing bullets → Tasks 1-6. Deferred items (leadership, full multi-college, hierarchical URLs, `organizations.metadata.full_name`) → Task 7 checklist. No gaps.

**Placeholders:** none — every code step shows the full code; every command shows expected output.

**Type consistency:** `faculty_slugs(org_id)`, `dept_orgs_of_college -> [{"node_id","slug","name","faculty"}]`, `org_node_by_slug -> int|None`, `college_name -> str`, `render_hub(college_name, cards)`, `build_dept(org, out_root, photo_map)`, `build_hub(out_root, college_node, depts)`, `leaderboard_path(out_root, segment)` — used identically across Tasks 1-7. `home_dept_segment` produced in Task 1, consumed in Task 4. Consistent.

**Note on `eval/questions.txt`:** the grow-correctness-suite rule targets the *bot* pipeline; FacultyFolio is a standalone static generator whose verification lives in its own pytest suite (grown in Tasks 1-6). No bot-eval questions apply.
