# FacultyFolio College-Hub Leadership + Rank Rollup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich the FacultyFolio college hub (`/ywcc/`) with a college-wide rank rollup, the department entry cards moved up, and Dean / Associate Deans / Department Chairs sections — all from existing KG data.

**Architecture:** Two pure read-only data functions (`db.college_leadership`, `rank.college_rollup` + `rank.college_chairs`) feed the existing person-card + glance markup, extracted into shared Jinja partials so the leaderboard and hub render identically. `build_college_hub` assembles the rows; `render_hub` grows optional `stats`/`leadership` kwargs (NJIT hub passes neither → unchanged).

**Tech Stack:** Python 3.11, Jinja2, SQLite (read-only via `db.connect()`), pytest. Static-site generator under `facultyfolio/`.

**Spec:** `docs/superpowers/specs/2026-07-09-facultyfolio-college-hub-leadership-design.md` (read §4 and §9 review log).

## Global Constraints

- Work on branch `feat/facultyfolio-college-hub-leadership` (already checked out; spec committed there).
- Tests run against the LIVE read-only DB (`db.connect()` → `config.DB_PATH`), asserting real YWCC values. Run all: `cd /home/md724/gsa-gateway && python3 -m pytest facultyfolio/tests -q`.
- No new crawl, no schema change, no writes. Data functions are read-only (`connect()` opens `mode=ro`).
- Honest-empty: a leadership section with no people is omitted; a person with no research areas shows no chips (never a placeholder).
- Displayed leadership title = the `titles[]` entry containing "dean" (verbatim selection, never reworded); names via `format.normalize_name`.
- Reuse existing helpers verbatim: `rank.roster`, `rank.by_rank`, `render._lb_row`, `_resolve_photo`. No duplicate logic.
- Commit after each task with an explicit `git add` of only the touched paths (never `git add -A`). No Claude/co-author attribution in commit messages.
- YWCC anchors for tests: `db.org_node_by_slug("ywcc")` → node 299; org node ids CS=16, Data Science=73, Informatics=(discover via `dept_orgs_of_college`). Expected rollup: **119 faculty, 76 on Scholar**; groups in ladder order `3 Department Chair, 6 Distinguished Professor, 13 Professor, 16 Associate Professor, 27 Assistant Professor, 31 Senior University Lecturer, 21 University Lecturer, 2 Faculty`. Leadership: Dean **Jamie Payton**; Associate Deans **Brook Wu**, **David Bader** (post-fix); Chairs **Vincent Oria** (Computer Science), **James Geller** (Data Science), **Michael Halper** (Informatics).

---

### Task 1: `db.college_leadership` — dean + associate deans

**Files:**
- Modify: `facultyfolio/db.py` (add function + a `_surname` helper + ensure `normalize_name` import)
- Test: `facultyfolio/tests/test_db_leadership.py` (create)

**Interfaces:**
- Produces: `db.college_leadership(college_node: int) -> {"dean": list[dict], "assoc_deans": list[dict]}` where each dict is `{"slug": str, "name": str, "title": str}` (name normalized "Given Surname"; title = the role/"dean" title).

- [ ] **Step 1: Write the failing test**

```python
# facultyfolio/tests/test_db_leadership.py
from facultyfolio import db

def _ywcc():
    return db.org_node_by_slug("ywcc")

def test_leadership_dean_and_assoc_deans_names_and_titles():
    lead = db.college_leadership(_ywcc())
    assert [d["name"] for d in lead["dean"]] == ["Jamie Payton"]
    assert lead["dean"][0]["title"] == "Dean, Ying Wu College of Computing"
    names = [a["name"] for a in lead["assoc_deans"]]
    assert names == ["David Bader", "Brook Wu"]          # normalized + surname-sorted
    titles = {a["name"]: a["title"] for a in lead["assoc_deans"]}
    assert titles["Brook Wu"] == "Associate Dean for Academic Affairs"
    assert titles["David Bader"] == "Associate Dean"     # role title, not "Distinguished Professor"
    # never the raw "Surname, Given" form
    assert all(", " not in a["name"] for a in lead["assoc_deans"])

def test_leadership_empty_for_a_department_node():
    # a department Org has no admin@ edges -> both lists empty (empty-safe)
    cs = 16
    lead = db.college_leadership(cs)
    assert lead == {"dean": [], "assoc_deans": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/md724/gsa-gateway && python3 -m pytest facultyfolio/tests/test_db_leadership.py -q`
Expected: FAIL with `AttributeError: module 'facultyfolio.db' has no attribute 'college_leadership'`

- [ ] **Step 3: Write minimal implementation**

Add to `facultyfolio/db.py` (confirm `from .format import normalize_name` is imported at top — add if missing):

```python
def _surname(name: str) -> str:
    parts = (name or "").split()
    return parts[-1].casefold() if parts else ""

def _role_title(titles: list) -> str:
    """The role title from a (rank + role) titles list: the entry containing 'dean';
    else the last entry. We SELECT which listed title to show, never reword it."""
    for t in titles or []:
        if "dean" in t.lower():
            return t
    return (titles or [""])[-1]

def college_leadership(college_node: int) -> dict:
    """Dean + associate deans from admin@college has_role edges. Names normalized to
    'Given Surname'; displayed title is the role ('dean') title, not the rank."""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT n.key AS key, n.name AS name, e.attrs AS attrs
               FROM edges e JOIN nodes n ON n.id=e.src_id
               WHERE e.type='has_role' AND e.category='admin'
                 AND e.dst_id=? AND e.is_active=1 AND n.is_active=1""",
            (college_node,),
        ).fetchall()
    finally:
        conn.close()
    dean, assoc = [], []
    for r in rows:
        attrs = json.loads(r["attrs"]) if r["attrs"] else {}
        title = _role_title(attrs.get("titles") or [])
        low = title.lower()
        if "dean" not in low:
            continue
        person = {"slug": r["key"].split("/")[-1],
                  "name": normalize_name(r["name"]), "title": title}
        (assoc if "associate dean" in low else dean).append(person)
    keyf = lambda p: (_surname(p["name"]), p["name"].casefold(), p["slug"])
    dean.sort(key=keyf); assoc.sort(key=keyf)
    return {"dean": dean, "assoc_deans": assoc}
```

(Confirm `json` is imported in `db.py` — it is used elsewhere in the file.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/md724/gsa-gateway && python3 -m pytest facultyfolio/tests/test_db_leadership.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/md724/gsa-gateway
git add facultyfolio/db.py facultyfolio/tests/test_db_leadership.py
git commit -m "feat(facultyfolio): db.college_leadership — dean + associate deans from KG"
```

---

### Task 2: `rank.college_rollup` + `rank.college_chairs`

**Files:**
- Modify: `facultyfolio/rank.py` (add two functions; reuse existing `roster`, `by_rank`, `_surname`, `import db`)
- Test: `facultyfolio/tests/test_rank_college.py` (create)

**Interfaces:**
- Consumes: `rank.roster(org_node_id)`, `rank.by_rank(roster)`, `db.dept_orgs_of_college`, `db.org_node_by_slug`.
- Produces:
  - `rank.college_rollup(college_node: int) -> {"total": int, "with_scholar": int, "groups": list[tuple[str,int]]}`
  - `rank.college_chairs(college_node: int) -> list[dict]` — each is a `roster` row plus `"dept_name": str`.

- [ ] **Step 1: Write the failing test**

```python
# facultyfolio/tests/test_rank_college.py
from facultyfolio import db, rank

def _ywcc():
    return db.org_node_by_slug("ywcc")

def test_rollup_totals_and_ladder_order():
    r = rank.college_rollup(_ywcc())
    assert r["total"] == 119 and r["with_scholar"] == 76
    assert r["groups"] == [
        ("Department Chair", 3), ("Distinguished Professor", 6), ("Professor", 13),
        ("Associate Professor", 16), ("Assistant Professor", 27),
        ("Senior University Lecturer", 31), ("University Lecturer", 21), ("Faculty", 2),
    ]

def test_rollup_total_equals_sum_of_group_counts():
    r = rank.college_rollup(_ywcc())
    assert sum(c for _, c in r["groups"]) == r["total"]

def test_rollup_no_duplicate_home_people():
    # the de-dup assert must hold on live data (119 distinct == 119)
    r = rank.college_rollup(_ywcc())          # would raise AssertionError if a dup-home existed
    assert r["total"] == 119

def test_chairs_one_per_dept_labeled_by_department():
    chairs = rank.college_chairs(_ywcc())
    by_name = {c["name"]: c["dept_name"] for c in chairs}
    assert by_name["Vincent Oria"] == "Computer Science"
    assert by_name["James Geller"] == "Data Science"
    assert by_name["Michael Halper"] == "Informatics"
    assert all(c["rank_index"] == 0 for c in chairs)
    assert len(chairs) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/md724/gsa-gateway && python3 -m pytest facultyfolio/tests/test_rank_college.py -q`
Expected: FAIL with `AttributeError: module 'facultyfolio.rank' has no attribute 'college_rollup'`

- [ ] **Step 3: Write minimal implementation**

Add to `facultyfolio/rank.py`:

```python
def college_rollup(college_node) -> dict:
    """College-wide rank rollup: concat every in-scope roster, rank ONCE.

    Org set = department children + the college node itself (mirrors db.college_coverage,
    catching faculty homed directly on the college org, e.g. a deptless college). Reusing
    by_rank on the combined list makes ladder order correct by construction — no merge logic.
    """
    org_ids = [d["node_id"] for d in db.dept_orgs_of_college(college_node)] + [college_node]
    combined = [row for oid in org_ids for row in roster(oid)]
    slugs = {r["slug"] for r in combined}
    assert len(slugs) == len(combined), (
        f"college_rollup: {len(combined) - len(slugs)} duplicate-home person(s) "
        "(multi-home producer regression) — the faculty headcount would inflate")
    return {
        "total": len(combined),
        "with_scholar": sum(1 for r in combined if r["citations"] is not None),
        "groups": [(g["label"], len(g["members"])) for g in by_rank(combined)],
    }

def college_chairs(college_node) -> list:
    """Every department chair (the rank_index==0 group members) tagged with dept_name.
    0 chairs in a dept -> none contributed; >1 -> all, surname-sorted."""
    out = []
    for d in db.dept_orgs_of_college(college_node):
        chairs = [r for r in roster(d["node_id"]) if r["rank_index"] == 0]
        for c in sorted(chairs, key=lambda r: (_surname(r["name"]), (r["name"] or "").casefold(), r["slug"])):
            row = dict(c)
            row["dept_name"] = d["name"]
            out.append(row)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/md724/gsa-gateway && python3 -m pytest facultyfolio/tests/test_rank_college.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/md724/gsa-gateway
git add facultyfolio/rank.py facultyfolio/tests/test_rank_college.py
git commit -m "feat(facultyfolio): rank.college_rollup + college_chairs (concat-then-by_rank, de-dup guard)"
```

---

### Task 3: Extract shared Jinja partials (`_person_card.html`, `_glance.html`) — pure refactor

**Files:**
- Create: `facultyfolio/templates/_person_card.html` (holds `photo_thumb` + `row_dir`)
- Create: `facultyfolio/templates/_glance.html` (holds `glance(stats)`)
- Modify: `facultyfolio/templates/leaderboard.html` (import both; delete the moved macro bodies + inline glance block; keep `row_cite`/`row_rising` local)
- Test: rely on EXISTING `facultyfolio/tests/test_render.py` (rank/citations/rising views) — must stay green.

**Interfaces:**
- Produces: importable macros `row_dir(row, asset_root)`, `photo_thumb(row, asset_root)` from `_person_card.html`; `glance(stats)` from `_glance.html`.

- [ ] **Step 1: Run the existing render tests to capture the green baseline**

Run: `cd /home/md724/gsa-gateway && python3 -m pytest facultyfolio/tests/test_render.py -q`
Expected: PASS (baseline — note the count).

- [ ] **Step 2: Create `_person_card.html`**

```jinja
{#- Shared person-card macros: used by the department leaderboard AND the college hub. -#}
{%- macro photo_thumb(row, asset_root) -%}
  {%- if row.photo_ref.startswith('monogram:') -%}
  <svg class="lb-photo" viewBox="0 0 40 40" role="img" aria-label="{{ row.name }}">
    <rect width="40" height="40" rx="20" fill="var(--hair)"/>
    <text x="20" y="26" text-anchor="middle" font-family="var(--f-display)" font-size="15" fill="var(--mute)">{{ row.photo_ref[9:] }}</text>
  </svg>
  {%- else -%}
  <img class="lb-photo" src="{{ asset_root }}{{ row.photo_ref }}" alt="{{ row.name }}" loading="lazy">
  {%- endif -%}
{%- endmacro -%}

{%- macro row_dir(row, asset_root) -%}
<a class="lb-row" href="{{ asset_root }}p/{{ row.slug }}.html"
   data-name="{{ row.name|lower }}" data-title="{{ row.title|lower }}" data-areas="{{ row.data_areas|lower }}">
  {{ photo_thumb(row, asset_root) }}
  <span class="lb-id"><span class="lb-name">{{ row.name }}</span>{% if row.title %}<span class="lb-title">{{ row.title }}</span>{% endif %}</span>
  {%- if row.areas %}<span class="lb-areas">{% for a in row.areas %}<span class="chip">{{ a }}</span>{% endfor %}</span>{% endif -%}
</a>
{%- endmacro -%}
```

- [ ] **Step 3: Create `_glance.html`**

```jinja
{#- Shared at-a-glance stats strip: department leaderboard AND college hub. -#}
{%- macro glance(stats) -%}
<div class="lb-glance">
  <span class="glance-hd"><strong>{{ stats.total }}</strong> faculty</span>
  <span class="glance-hd"><strong>{{ stats.with_scholar }}</strong> on Google Scholar</span>
  {% for label, count in stats.groups %}<span class="glance-g">{{ count }} · {{ label }}</span>{% endfor %}
</div>
{%- endmacro -%}
```

- [ ] **Step 4: Rewire `leaderboard.html`**

At the very top of `leaderboard.html` (after `{% extends %}`), add:

```jinja
{% from "_person_card.html" import photo_thumb, row_dir %}
{% from "_glance.html" import glance %}
```

Delete the now-duplicated `photo_thumb` and `row_dir` macro definitions from `leaderboard.html` (leave `row_cite` and `row_rising` — they call the now-imported `photo_thumb`). Replace the inline `<div class="lb-glance">…</div>` block (the two `.glance-hd` + `{% for %}` `.glance-g`) with:

```jinja
{{ glance(stats) }}
```

- [ ] **Step 5: Run render tests — verify identical output**

Run: `cd /home/md724/gsa-gateway && python3 -m pytest facultyfolio/tests/test_render.py facultyfolio/tests/test_render_hub.py -q`
Expected: PASS, same count as Step 1 baseline (the citations/rising tests confirm `photo_thumb` still resolves).

- [ ] **Step 6: Commit**

```bash
cd /home/md724/gsa-gateway
git add facultyfolio/templates/_person_card.html facultyfolio/templates/_glance.html facultyfolio/templates/leaderboard.html
git commit -m "refactor(facultyfolio): extract person-card + glance macros to shared partials"
```

---

### Task 4: `render_hub` grows `stats` + `leadership`; `hub.html` renders them

**Files:**
- Modify: `facultyfolio/render.py` (`render_hub` signature + passthrough)
- Modify: `facultyfolio/templates/hub.html` (glance, dept cards moved up, leadership sections)
- Test: `facultyfolio/tests/test_render_hub.py` (extend)

**Interfaces:**
- Consumes: `_glance.html`'s `glance`, `_person_card.html`'s `row_dir`; `_lb_row`-shaped rows.
- Produces: `render.render_hub(title, cards, *, eyebrow, asset_root, canonical=None, nav=None, og_title=None, og_description=None, stats=None, leadership=None)`. `leadership` = `{"dean": [rows], "assoc_deans": [rows], "chairs": [rows]}`, each row an `_lb_row` dict.

- [ ] **Step 1: Write the failing test**

```python
# add to facultyfolio/tests/test_render_hub.py
from facultyfolio import render

def _row(slug, name, title, areas=()):
    return render._lb_row(
        {"slug": slug, "name": name, "title": title, "areas": list(areas),
         "citations": None, "h_index": None, "rank_num": None}, {})

def test_hub_renders_stats_departments_then_leadership_in_order():
    stats = {"total": 119, "with_scholar": 76,
             "groups": [("Department Chair", 3), ("Professor", 13)]}
    leadership = {
        "dean": [_row("js2852", "Jamie Payton", "Dean, Ying Wu College of Computing")],
        "assoc_deans": [_row("bader", "David Bader", "Associate Dean")],
        "chairs": [_row("oria", "Vincent Oria", "Department Chair, Computer Science", ["Databases"])],
    }
    html = render.render_hub("Ying Wu College of Computing", CARDS, eyebrow="College",
                             asset_root="../", stats=stats, leadership=leadership)
    # stats chips
    assert "119" in html and "3 · Department Chair" in html
    # ordering: departments block appears before the Dean section
    assert html.index("computer-science/index.html") < html.index("Jamie Payton")
    # leadership present + linked + area chip
    assert 'href="../p/bader.html"' in html and "David Bader" in html
    assert "Department Chair, Computer Science" in html
    assert '<span class="chip">Databases</span>' in html

def test_hub_without_stats_or_leadership_is_unchanged():
    html = render.render_hub("New Jersey Institute of Technology", CARDS,
                             eyebrow="University", asset_root="")
    assert "lb-glance" not in html          # no stats block
    assert "Dean" not in html               # no leadership sections
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/md724/gsa-gateway && python3 -m pytest facultyfolio/tests/test_render_hub.py -q`
Expected: FAIL (`render_hub() got an unexpected keyword argument 'stats'`).

- [ ] **Step 3: Implement `render_hub` kwargs**

In `facultyfolio/render.py`, update `render_hub` signature and render call:

```python
def render_hub(title: str, cards: list, *, eyebrow: str, asset_root: str,
               canonical: str = None, nav: list = None,
               og_title: str = None, og_description: str = None,
               stats: dict = None, leadership: dict = None) -> str:
    return _env.get_template("hub.html").render(
        college_name=title, eyebrow=eyebrow, cards=cards,
        asset_root=asset_root, canonical=canonical,
        nav=nav or [], og_title=og_title or title, og_description=og_description,
        claim_url=config.CLAIM_MAILTO,
        stats=stats, leadership=leadership or {})
```

- [ ] **Step 4: Update `hub.html`**

Add imports at top (after `{% extends %}`):

```jinja
{% from "_glance.html" import glance %}
{% from "_person_card.html" import row_dir %}
```

Replace the body `<section>` content so it renders, in order: eyebrow+title+rule, stats (if any), department `hub-cards`, then leadership sections. Use this section body:

```jinja
      <p class="eyebrow">{{ eyebrow }}</p>
      <h2>{{ college_name }}</h2>
      <div class="rule"></div>

      {% if stats %}{{ glance(stats) }}{% endif %}

      <div class="lb-group">
        {% if leadership.dean or leadership.assoc_deans or leadership.chairs %}<h3 class="lb-group-h">Departments</h3>{% endif %}
        <div class="hub-cards">
          {% for c in cards %}
          <a class="hub-card" href="{{ c.url }}">
            {% if c.badge %}<span class="hub-badge" aria-hidden="true">{{ c.badge }}</span>{% endif %}
            <span class="hub-name">{{ c.name }}</span>
            <span class="hub-stats"><strong>{{ c.faculty }}</strong> faculty · <strong>{{ c.scholar }}</strong> on Google Scholar</span>
            <span class="hub-go">View directory →</span>
          </a>
          {% endfor %}
        </div>
      </div>

      {% if leadership.dean %}
      <div class="lb-group"><h3 class="lb-group-h">Dean</h3>
        {% for row in leadership.dean %}{{ row_dir(row, asset_root) }}{% endfor %}
      </div>{% endif %}

      {% if leadership.assoc_deans %}
      <div class="lb-group"><h3 class="lb-group-h">Associate Deans</h3>
        {% for row in leadership.assoc_deans %}{{ row_dir(row, asset_root) }}{% endfor %}
      </div>{% endif %}

      {% if leadership.chairs %}
      <div class="lb-group"><h3 class="lb-group-h">Department Chairs</h3>
        {% for row in leadership.chairs %}{{ row_dir(row, asset_root) }}{% endfor %}
      </div>{% endif %}
```

(The `Departments` header only shows when there's leadership below it, so the NJIT hub — no leadership — keeps its current bare card grid.)

- [ ] **Step 5: Run tests to verify pass (and NJIT-hub unchanged)**

Run: `cd /home/md724/gsa-gateway && python3 -m pytest facultyfolio/tests/test_render_hub.py -q`
Expected: PASS (all, including `test_hub_without_stats_or_leadership_is_unchanged`).

- [ ] **Step 6: Commit**

```bash
cd /home/md724/gsa-gateway
git add facultyfolio/render.py facultyfolio/templates/hub.html facultyfolio/tests/test_render_hub.py
git commit -m "feat(facultyfolio): render_hub stats+leadership; hub shows stats, dept cards, leadership"
```

---

### Task 5: Wire it into `build_college_hub`

**Files:**
- Modify: `facultyfolio/build.py` (`build_college_hub` signature + body; `build_site` passes `photo_map`; add `_leadership_row` helper)
- Test: `facultyfolio/tests/test_build.py` (extend) — full-build assertion on `/ywcc/index.html`.

**Interfaces:**
- Consumes: `db.college_leadership`, `rank.college_rollup`, `rank.college_chairs`, `render._lb_row`, `_resolve_photo`, `db.get_faculty`.
- Produces: `build_college_hub(college_node, college_seg, out_root, photo_map=None)`.

- [ ] **Step 1: Write the failing test**

```python
# add to facultyfolio/tests/test_build.py — mirror the existing full-build test's setup
def test_college_hub_has_stats_and_leadership(tmp_path, monkeypatch):
    from facultyfolio import build
    monkeypatch.setattr(build, "photos_ensure", lambda slug, *a, **k: f"monogram:{slug[:2].upper()}")
    build.build_site(scope={"college": "ywcc"}, out_root=str(tmp_path))
    html = (tmp_path / "ywcc" / "index.html").read_text()
    assert "119" in html and "3 · Department Chair" in html      # rollup
    assert "Jamie Payton" in html                                # dean
    assert "David Bader" in html and "Brook Wu" in html          # assoc deans (post-fix)
    assert "Vincent Oria" in html and "Department Chair, Computer Science" in html
    # each leader linked to their profile
    assert 'p/bader.html' in html and 'p/oria.html' in html
```

(If `test_build.py` already has a helper/fixture for building, follow its pattern; the scope-build call above is self-contained.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/md724/gsa-gateway && python3 -m pytest facultyfolio/tests/test_build.py::test_college_hub_has_stats_and_leadership -q`
Expected: FAIL (stats/leadership not yet rendered by the build).

- [ ] **Step 3: Implement the build wiring**

In `facultyfolio/build.py`, add a helper and update `build_college_hub`:

```python
def _leadership_row(person, photo_map, assets_dir, *, title):
    """Turn a leadership/chair person into a render row, reusing the dept person-card path.
    `person` needs a `slug`; `title` is the display title (role for deans, 'Department Chair,
    <dept>' for chairs). Areas + photo come from the same source the dept pages use."""
    slug = person["slug"]
    f = db.get_faculty(slug)
    if slug not in photo_map:
        photo_map[slug] = _resolve_photo(slug, f, assets_dir)
    return render._lb_row({
        "slug": slug, "name": f["name"], "title": title, "areas": f["areas"],
        "citations": None, "h_index": None, "rank_num": None,
    }, photo_map)
```

Then in `build_college_hub` add the `photo_map=None` parameter and, before the `render.render_hub(...)` call, build `stats` + `leadership`:

```python
def build_college_hub(college_node: int, college_seg: str, out_root: str, photo_map: dict = None) -> str:
    depts = db.dept_orgs_of_college(college_node)
    cards = []
    for d in depts:
        n, m = rank.coverage(d["node_id"])
        cards.append({"name": d["name"], "faculty": m, "scholar": n,
                      "url": f"{d['slug']}/index.html", "badge": _org_badge(d["slug"], d["name"])})
    photo_map = dict(photo_map or {})
    assets = paths.assets_dir(out_root)
    stats = rank.college_rollup(college_node)
    lead = db.college_leadership(college_node)
    leadership = {
        "dean": [_leadership_row(p, photo_map, assets, title=p["title"]) for p in lead["dean"]],
        "assoc_deans": [_leadership_row(p, photo_map, assets, title=p["title"]) for p in lead["assoc_deans"]],
        "chairs": [_leadership_row(c, photo_map, assets, title=f"Department Chair, {c['dept_name']}")
                   for c in rank.college_chairs(college_node)],
    }
    canonical = paths.canonical_url(f"{college_seg}/")
    cname = db.college_name(college_node)
    _, cm = db.college_coverage(college_node)
    nav = _crumbs("../", [("NJIT", ""), (cname, None)])
    og = f"{cm} faculty across {len(depts)} departments at {cname}, NJIT — with Google Scholar metrics."
    html = render.render_hub(cname, cards, eyebrow="College",
                             asset_root="../", canonical=canonical,
                             nav=nav, og_title=cname, og_description=og,
                             stats=stats, leadership=leadership)
    path = paths.college_hub_path(out_root, college_seg)
    _write(path, html)
    return path
```

In `build_site`, update the hub call to pass the shared `photo_map`:

```python
        build_college_hub(cnode, cslug, out_root, photo_map=photo_map)
```

(Confirm `render` and `rank` are already imported in `build.py`; they are.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/md724/gsa-gateway && python3 -m pytest facultyfolio/tests/test_build.py -q`
Expected: PASS (new test + existing build tests green).

- [ ] **Step 5: Commit**

```bash
cd /home/md724/gsa-gateway
git add facultyfolio/build.py facultyfolio/tests/test_build.py
git commit -m "feat(facultyfolio): build_college_hub assembles stats + leadership from KG"
```

---

### Task 6: Full-suite green + real-build visual verification

**Files:** none (verification only).

- [ ] **Step 1: Run the full FacultyFolio test suite**

Run: `cd /home/md724/gsa-gateway && python3 -m pytest facultyfolio/tests -q`
Expected: ALL PASS (no regressions in build/render/rank/db/scoped tests).

- [ ] **Step 2: Do a real build into a scratch dir and screenshot `/ywcc/`**

```bash
cd /home/md724/gsa-gateway
python3 -c "from facultyfolio import build; build.build_site(scope={'college':'ywcc'}, out_root='/tmp/ff_verify')"
google-chrome-stable --headless --disable-gpu --no-sandbox --hide-scrollbars \
  --window-size=1200,1600 --virtual-time-budget=4000 \
  --screenshot=/tmp/ff_verify/ywcc.png "file:///tmp/ff_verify/ywcc/index.html"
```

Expected: page shows title → stats chips → department cards → Dean (Payton) → Associate Deans (Bader, Wu) → Department Chairs (Oria/Geller/Halper), each linked, area chips present, Halper chip-less. (Note: a local file build may show monogram placeholders instead of photos if `assets/photos/` isn't populated in the scratch dir — that's a build-asset detail, not a page-structure defect; confirm structure + text.)

- [ ] **Step 3: Eyeball the six leadership cards' area chips (pre-deploy honesty check, §6a)**

Confirm Payton / Wu / Bader / Oria / Geller area chips are clean (not `<br>`-garbled); Halper legitimately empty. Note any garble for a follow-up data patch — do NOT block on it (consistent with the dept pages).

- [ ] **Step 4: Fill the spec's §7 goals checklist** (shipped/deferred) and update the PR description. No code change.

- [ ] **Step 5: Hand off for review** — do NOT deploy to the live `facultyfolio.github.io` Pages output. Present the diff + the `/ywcc/` screenshot to the owner for sign-off; deploy (rebuild + push Pages) is a separate owner-gated step.
