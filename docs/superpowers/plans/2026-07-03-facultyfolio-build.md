# FacultyFolio Generator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only, KG-driven static-site generator that emits one HTML page per NJIT CS faculty member plus a CS leaderboard, matching the reference design in `docs/samplepages/`.

**Architecture:** Layered — `db.py` (DB→dict, read-only) → `rank.py` (dept rank/coverage) → `format.py` + `chart.py` (pure string/SVG transforms) → `render.py` (pure dict→HTML via Jinja2) → `build.py` (orchestrate). Pure functions are built and tested first (no DB); `db.py` is the only SQLite consumer.

**Tech Stack:** Python 3.11, Jinja2 3.1, stdlib `sqlite3` (`mode=ro`), `urllib`/`requests` for one-time asset fetches.

**Full design:** `docs/superpowers/specs/2026-07-03-facultyfolio-generator-design.md` (rev 4, APPROVED-FOR-BUILD). Read it — every rule below traces to a spec section.

## Global Constraints

- **Read-only DB.** Every connection: `sqlite3.connect("file:gsa_gateway.db?mode=ro", uri=True)` + `conn.execute("PRAGMA query_only=ON")`. No writes, ever. [spec §1]
- **Trust boundary.** Publishable prose = `knowledge_items` WHERE `created_by='crawler'` AND `type IN ('education','teaching','profile')`. Never emit `type='about'`. [spec §3]
- **Strict-mechanical formatting only** — no maintained lookup/abbreviation tables. [spec §3.4, §7]
- **CS org id = 16.** Koutis test node id = 33. Fixed heading = "Impact & trajectory". 4th-stat label = "Active since".
- **Determinism/idempotency:** research areas ordered by edge id; teaching entries by lowest course number; two builds → byte-identical output.
- **Output tree** (separate from repo code): `Faculty-Folio/{p/<slug>.html, cs/index.html, assets/{style.css, photos/, fonts/}}`.
- **Module home:** `facultyfolio/` package in the repo. Tests: `facultyfolio/tests/`.
- **Commits:** frequent, one per task; no Claude attribution in messages.

---

### Task 1: Package scaffold + config

**Files:**
- Create: `facultyfolio/__init__.py`, `facultyfolio/config.py`, `facultyfolio/tests/__init__.py`
- Test: `facultyfolio/tests/test_config.py`

**Interfaces:**
- Produces: `config.DB_PATH:str`, `config.OUT_ROOT:str`, `config.CS_ORG_ID=16`, `config.KOUTIS_NODE=33`, `config.FIXED_HEADING="Impact & trajectory"`, `config.SUPPRESSED:set[str]` (slugs), `config.SYNC_FMT` helper spec.

- [ ] **Step 1: Write the failing test**
```python
from facultyfolio import config
def test_config_constants():
    assert config.CS_ORG_ID == 16
    assert config.FIXED_HEADING == "Impact & trajectory"
    assert isinstance(config.SUPPRESSED, (set, frozenset))
    assert config.OUT_ROOT.endswith("Faculty-Folio")
```
- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError`). `python -m pytest facultyfolio/tests/test_config.py -v`
- [ ] **Step 3: Implement** `config.py` with the constants above; `SUPPRESSED = set()` (empty; the wired hook); `DB_PATH` resolves the repo `gsa_gateway.db`; `OUT_ROOT` = a sibling `Faculty-Folio` dir (configurable via env `FACULTYFOLIO_OUT`).
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** `feat(facultyfolio): package scaffold + config constants`

---

### Task 2: `format.py` — mechanical formatters (pure) [spec §7]

**Files:**
- Create: `facultyfolio/format.py`
- Test: `facultyfolio/tests/test_format.py`

**Interfaces:**
- Produces: `normalize_name(s)->str`, `smart_titlecase(s)->str`, `clean_mojibake(s)->str`, `initials(name)->str`, `format_venue(raw)->str`, `format_teaching(raw)->list[str]`, `format_education(raw)->list[str]`, `format_office(raw)->str`, `commafy(n)->str`.

- [ ] **Step 1: Write failing tests** (one function per concern; real strings from the live KG):
```python
from facultyfolio import format as F

def test_normalize_name():
    assert F.normalize_name("Koutis, Ioannis") == "Ioannis Koutis"
    assert F.normalize_name("Kieran Murphy") == "Kieran Murphy"
    assert F.normalize_name("Smith, John, Jr") == "Smith, John, Jr"  # multi-comma untouched
    assert F.initials("Ioannis Koutis") == "IK"

def test_smart_titlecase():
    assert F.smart_titlecase("EXPLAINABLE AI") == "Explainable AI"      # acronym preserved
    assert F.smart_titlecase("INTRO TO MACHINE LEARNING-HONORS").startswith("Intro To Machine")

def test_format_venue():
    raw1 = "Foundations of Computer Science (FOCS), 2010 51st Annual IEEE Symposium on�…, 2010"
    assert F.format_venue(raw1) == "FOCS 2010"
    raw2 = "arXiv preprint arXiv:2604.20078, 2026"
    assert F.format_venue(raw2) == "arXiv 2026"
    # no-acronym branch: honest fragment + year, NOT "FOCS 2011"
    raw3 = "Proceedings of the 2011 IEEE 52st Annual Symposium on Foundations of�…, 2011"
    assert F.format_venue(raw3).endswith("2011") and "FOCS" not in F.format_venue(raw3)

def test_format_teaching():
    raw = ("Past Courses; CS 375: INTRO TO MACHINE LEARNING-HONORS CS 435: ADV DATA STRUCT-ALG DES "
           "CS 610: DATA STRUCTURE & ALG CS 610: DATA STRUCTURES AND ALGORITHMS CS 611: COMPUTABILITY "
           "& COMPLEX CS 675: MACHINE LEARNING CS 677: DEEP LEARNING DS 675: MACHINE LEARNING "
           "DS 677: DEEP LEARNING")
    out = F.format_teaching(raw)
    assert "Machine Learning (CS 675 / DS 675)" in out          # cross-list grouped by title
    assert any(e.startswith("Data Structures And Algorithms") and "CS 610" in e for e in out)  # variant collapsed to longest
    assert all("ST:" not in e for e in out)

def test_format_teaching_special_topics():
    raw = "Past Courses; CS 485: ST: EXPLAINABLE AI CS 698: ST:EXPLAINABLE AI CS 785: ST: EXPLAINABLE AI"
    assert F.format_teaching(raw) == ["Explainable AI (CS 485 / CS 698 / CS 785)"]

def test_format_education_4field():
    raw = ("Education of Ioannis Koutis (Computer Science): Ph.D.; Carnegie Mellon University; "
           "Computer Science; 2007; Diploma; University of Patras; Computer Engineering and Informatics; 1998")
    out = F.format_education(raw)
    assert out[0] == "Ph.D. Computer Science, Carnegie Mellon University (2007)"
    assert len(out) == 2

def test_format_education_3field():   # B5 — variable-length record
    raw = ("Education of James Calvin (Computer Science): Ph.D.; Stanford University; 1990; "
           "M.S.; University of California-Berkeley; 1979; B.A.; University of California-Berkeley; 1978")
    out = F.format_education(raw)
    assert out[0] == "Ph.D., Stanford University (1990)"     # no field segment
    assert len(out) == 3

def test_format_education_degree_only_omitted():
    assert F.format_education("Education of Vincent Oria (Computer Science): Ph.D.") == []

def test_format_office():
    assert F.format_office("4105 Guttenberg Information Technologies Center (GITC)") == "4105 GITC"
```
- [ ] **Step 2: Run — expect FAIL** (`AttributeError`/`ModuleNotFoundError`).
- [ ] **Step 3: Implement `format.py`** per spec §7:
  - `clean_mojibake`: drop `�`, normalize whitespace, strip stray `…` runs.
  - `normalize_name`: `re.fullmatch(r"([^,]+),\s+([^,]+)", s)` → `"{2} {1}"` else `s`.
  - `initials`: first letter of first + last token, upper.
  - `smart_titlecase`: split on spaces; for each token keep verbatim if `token.isupper() and len(token)<=3` else `.title()`.
  - `format_venue`: clean; `arXiv:` → `f"arXiv {year}"`; else if `(ACRONYM)` present (regex `\(([A-Z]{2,})\)`) use it; else longest segment before first comma; strip `\d+(st|nd|rd|th)\s+Annual`, `IEEE Symposium on`, `Proceedings of the`; append the trailing 4-digit year (dedupe repeats).
  - `format_teaching`: strip `^Past Courses;?\s*` after the `:`-prefix (already removed by caller or here); split on `(?=[A-Z]{2,4}\s?\d{3}:)`; parse `(code, title)`; clean title (`^ST:?\s*` strip + `smart_titlecase`); **Pass 1** dict `full_code -> longest cleaned title`; **Pass 2** dict `normed_title -> sorted set of codes`; emit `"{title} ({/ }.join(codes))"` when >1 code else `"{title}"` if a bare single-code course... (match reference: single-code courses render title only, e.g. "Intro To Machine Learning-Honors"); order entries by min course number.
  - `format_education`: strip `^Education of .*?:\s*`; split `;`; year-anchored accumulate; render `"{degree}{ ' '+field if field}"`+`", {institution} ({year})"` — when no field: `"{degree}, {institution} ({year})"`.
  - `format_office`: reuse the `(ACRONYM)` rule → `"{leading} {ACRONYM}"`.
  - `commafy`: `f"{n:,}"`.
- [ ] **Step 4: Run — expect PASS** (all `test_format.py`).
- [ ] **Step 5: Commit** `feat(facultyfolio): mechanical formatters (name/venue/teaching/education/office)`

---

### Task 3: `chart.py` — citations-per-year SVG (pure) [spec §6]

**Files:** Create `facultyfolio/chart.py`; Test `facultyfolio/tests/test_chart.py`

**Interfaces:**
- Produces: `render_chart(cites_per_year:dict[str,int], sync_year:int) -> str|None` (returns `None` when the render gate fails).

- [ ] **Step 1: Failing tests**
```python
from facultyfolio import chart as C
KOUTIS = {"2007":8,"2008":13,"2009":37,"2010":62,"2011":107,"2012":97,"2013":152,"2014":157,
          "2015":203,"2016":169,"2017":161,"2018":161,"2019":208,"2020":163,"2021":140,"2022":151,
          "2023":174,"2024":194,"2025":251,"2026":152}
def test_peak_excludes_partial():
    svg = C.render_chart(KOUTIS, 2026)
    assert 'class="bar peak"' in svg and "251" in svg          # 2025 is peak
    assert '2026: 152 (partial)' in svg                         # latest==sync → partial
def test_partial_only_when_latest_eq_sync():
    eska = {"2018":2,"2019":3,"2020":5,"2021":8}                # latest 2021, sync 2026
    svg = C.render_chart(eska, 2026)
    assert "(partial)" not in svg and 'class="bar peak"' in svg # 2021 is a full, peak-eligible bar
def test_gate_min_years():
    assert C.render_chart({"2024":1,"2025":2}, 2026) is None    # <4 years
def test_gate_peak_zero():
    assert C.render_chart({"2020":0,"2021":0,"2022":0,"2023":0}, 2026) is None  # peak==0
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** per spec §6: gate (`len>=4` and `peak>0`); partial = latest year iff `== sync_year`; `peak = max(full years)`; `scale=108/peak`; bar geom `viewBox 0 0 660 134`, baseline 116; classes `bar`/`bar peak`/`bar partial`; axis labels first/peak/last; peak label; `<title>` tooltips; `role="img"` + aria-label.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** `feat(facultyfolio): cites-per-year SVG chart with partial-year + peak guards`

---

### Task 4: `db.py` — faculty dict (read-only golden) [spec §2, §3]

**Files:** Create `facultyfolio/db.py`; Test `facultyfolio/tests/test_db.py`

**Interfaces:**
- Produces: `connect()->sqlite3.Connection` (ro), `get_faculty(slug|node_id)->dict`, `cs_faculty_slugs()->list[str]`.
- Dict shape: `{slug, name(normalized), title, home_dept, joint_dept, college, email, phone, office, profiles{scholar,linkedin,github,website,orcid}, areas:[str], education_raw, teaching_raw, scholar:{...}|None, suppressed:bool}`. **Formatters are NOT applied here** (render applies them) EXCEPT `normalize_name` (identity-level).

- [ ] **Step 1: Failing golden test** (Koutis, node 33):
```python
from facultyfolio import db
def test_koutis_dict():
    f = db.get_faculty(33)
    assert f["name"] == "Ioannis Koutis"            # normalized from "Koutis, Ioannis" (B1)
    assert f["title"] == "Associate Professor"
    assert f["home_dept"] == "Computer Science"
    assert f["college"] == "Ying Wu College of Computing"
    assert "4105" in f["office"]
    assert set(f["profiles"]) >= {"scholar","linkedin","github","website"}
    assert len(f["areas"]) == 5 and len(set(a.lower().replace(" ","") for a in f["areas"])) == 5  # deduped
    assert f["scholar"]["citations"] == 2791
def test_readonly_connection_rejects_write():
    import pytest, sqlite3
    conn = db.connect()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE x(y)")
def test_trust_boundary_excludes_about():
    f = db.get_faculty(33)
    assert "about" not in (f.get("_prose_types") or [])   # only crawler profile/education/teaching read
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement `db.py`:** ro connect; resolve node by id or `slug`→key; read attrs; home/joint role edges (`category`), titles join; org tree via `part_of` for college; `researches` edges `is_active=1` ordered by edge id + normalized-key dedup (B6); prose via `metadata LIKE '%"entity_id": "<key>"%'` AND `created_by='crawler'` AND type filter; scholar bag or `None`; `suppressed = slug in config.SUPPRESSED`.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** `feat(facultyfolio): read-only faculty dict from KG (Koutis golden)`

---

### Task 5: `rank.py` — dept rank + coverage [spec §8]

**Files:** Create `facultyfolio/rank.py`; Test `facultyfolio/tests/test_rank.py`

**Interfaces:**
- Produces: `ranked_list(org_id)->list[dict{slug,name,citations,h_index,rank}]`, `coverage(org_id)->tuple[int,int]` (N,M).

- [ ] **Step 1: Failing test**
```python
from facultyfolio import rank
from facultyfolio import config
def test_cs_coverage_and_head():
    N, M = rank.coverage(config.CS_ORG_ID)
    assert (N, M) == (39, 57)
    lst = rank.ranked_list(config.CS_ORG_ID)
    assert lst[0]["citations"] >= lst[1]["citations"]     # descending
    assert lst[0]["rank"] == 1
    assert len(lst) == 39
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement:** M = active `has_role category='faculty'`→org members; N = those with integer scholar citations; sort desc; assign 1-based rank; exclude `config.SUPPRESSED`.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** `feat(facultyfolio): department ranking + coverage denominator`

---

### Task 6: `render.py` + `templates/` — profile HTML (pure) [spec §4, §5]

**Files:**
- Create: `facultyfolio/render.py`, `facultyfolio/templates/base.html`, `facultyfolio/templates/profile.html`
- Test: `facultyfolio/tests/test_render.py` (+ fixtures dir)

**Interfaces:**
- Consumes: a faculty dict (Task 4 shape) + formatters (Task 2) + chart (Task 3).
- Produces: `render_profile(faculty:dict)->str`.

**Template note:** `base.html` reproduces the reference shell (nav, provenance rail, grid, footer, toggle `<script>`) and links `../assets/style.css` + `assets/fonts` (via style.css). `profile.html` extends it with the 5 sections. Copy the exact HTML structure from `docs/samplepages/koutis.html` — it IS the template; parameterize the data.

- [ ] **Step 1: Failing golden + degradation tests**
```python
from facultyfolio import render, db
def test_profile_koutis_sections():
    html = render.render_profile(db.get_faculty(33))
    for s in ("Areas of focus","Background","Impact & trajectory","Selected work","Awards"):
        assert s in html
    assert "Ioannis Koutis" in html and "Koutis, Ioannis" not in html
    assert "Active since 2007" in html and "Dept. rank" not in html   # rank cut
    assert 'class="bar peak"' in html
def test_profile_junior_no_office_row(monkeypatch):
    f = db.get_faculty("km982")           # Kieran — no office/phone
    html = render.render_profile(f)
    assert "Joint appointment" in html and ">Office<" not in html
def test_profile_degraded_education_omits_row():
    f = db.get_faculty("oria")            # education == "Ph.D." only
    assert ">Education<" not in render.render_profile(f)
def test_missing_scholar_single_hook():
    f = db.get_faculty(33); f["scholar"] = None
    html = render.render_profile(f)
    assert html.count('class="hook"') == 2      # missing-scholar hook + Recognition (positive)
    assert "No Google Scholar profile" in html
def test_worst_case_no_scholar_no_areas():
    f = db.get_faculty(33); f["scholar"]=None; f["areas"]=[]
    html = render.render_profile(f)
    assert html.count('class="hook"') == 3      # research + scholar + recognition, within budget
def test_no_llm_prose_leaks():
    html = render.render_profile(db.get_faculty(33))
    assert "not written or generated" in html   # provenance label present
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement `render.py`** (Jinja2 `Environment`, autoescape on) + templates; apply formatters; conditional rows/sections per spec §4; monogram fallback placeholder call into `photos` (Task 7) via a passed `photo_ref`.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** `feat(facultyfolio): profile renderer + templates (golden + degradation)`

---

### Task 7a: `scripts/capture_njit_photos.py` — gather NJIT card photo URLs (gated DB write) [spec §4a]

**Files:** Create `scripts/capture_njit_photos.py`; Test `facultyfolio/tests/test_capture_photos.py` (parser unit test only — no live fetch in tests)

**Interfaces:**
- Produces: `extract_photo_url(html, base)->str|None` (pure parser), `main(--commit)` (gated writer of `attrs.profiles.njit_photo`).

- [ ] **Step 1: Failing parser test** (pure, on a saved NJIT profile HTML fixture):
```python
from scripts.capture_njit_photos import extract_photo_url
def test_extract_headshot():
    html = open("facultyfolio/tests/fixtures/njit_profile_sample.html").read()
    url = extract_photo_url(html, "https://people.njit.edu")
    assert url and url.startswith("http") and any(url.lower().endswith(e) for e in (".jpg",".jpeg",".png"))
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement:** `extract_photo_url` = structural selector for the profile headshot `<img>` (mechanical, no interpretation); `main` fetches each CS slug's page (project UA), extracts, and MERGES `attrs.profiles.njit_photo={url}` into the node; **gated** — `hardened_backup` + dry-run default + `--commit`; prints the found-URL table on dry-run.
- [ ] **Step 4: Run parser test — expect PASS.** Then **dry-run `main`**, show the owner the URL table, get `--commit` approval (do NOT commit the DB write without it).
- [ ] **Step 5: Commit** the SCRIPT `feat(facultyfolio): gated NJIT people-card photo-URL capture` (the DB write is a separate gated live op, not a repo commit).

### Task 7b: `photos.py` — Scholar→NJIT→monogram + download [spec §4]

**Files:** Create `facultyfolio/photos.py`; Test `facultyfolio/tests/test_photos.py`

**Interfaces:**
- Produces: `ensure_photo(slug, scholar_photo_url, njit_photo_url, name, out_dir)->str` (returns relative ref `../assets/photos/<slug>.jpg` OR a `monogram:<INITIALS>` sentinel the template renders as SVG).

- [ ] **Step 1: Failing tests**
```python
from facultyfolio import photos
SIL = "https://scholar.google.com/citations/images/avatar_scholar_128.png"
def test_scholar_first(tmp_path, monkeypatch):
    # real scholar url wins; stub fetch to write bytes
    ...
def test_silhouette_falls_to_njit(tmp_path, monkeypatch):
    ref = photos.ensure_photo("oria", SIL, "https://people.njit.edu/img/oria.jpg", "Vincent Oria", tmp_path)
    assert "oria.jpg" in ref            # NJIT used, not monogram
def test_no_photo_anywhere_monogram(tmp_path):
    ref = photos.ensure_photo("calvin", SIL, None, "James Calvin", tmp_path)
    assert ref == "monogram:JC"
def test_cached_not_redownloaded(tmp_path, monkeypatch):
    # second call must not re-fetch (idempotency)
    ...
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement:** order Scholar (non-silhouette) → NJIT → monogram; detect silhouette by URL `avatar_scholar_128.png` and/or byte-hash; download (project UA, no personal email — [[feedback_outbound_personal_data]]) to `<out>/photos/<slug>.jpg`, skip if exists; monogram = `f"monogram:{initials(name)}"`.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** `feat(facultyfolio): photo resolution (scholar→njit→monogram) + download`

---

### Task 8: `assets.py` — stylesheet extraction + self-hosted fonts [spec §5]

**Files:** Create `facultyfolio/assets.py`, `facultyfolio/assets/style.css` (extracted verbatim from the reference `<style>` with tokens on top + `@font-face` block); Test `facultyfolio/tests/test_assets.py`

**Interfaces:**
- Produces: `copy_assets(out_root)->None` (writes `style.css`, vendors `fonts/*.woff2`).

- [ ] **Step 1: Failing test:** after `copy_assets(tmp)`, assert `style.css` exists, contains `:root{` tokens and `@font-face`, and `fonts/` has the 3 families.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement:** extract the reference CSS to `facultyfolio/assets/style.css`; add `@font-face` for Fraunces 500/600, Inter 400/500/600, IBM Plex Mono 400/500 → vendor woff2 into `assets/fonts/` (one-time fetch script; commit the files); `copy_assets` copies both into `out_root/assets/`.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** `feat(facultyfolio): shared tokenised stylesheet + self-hosted fonts`

---

### Task 9: `leaderboard.html` + `render_leaderboard` [spec §9]

**Files:** Create `facultyfolio/templates/leaderboard.html`; Modify `facultyfolio/render.py`; Test `facultyfolio/tests/test_render.py`

**Interfaces:**
- Consumes: `rank.ranked_list` + `rank.coverage`.
- Produces: `render.render_leaderboard(org_name, ranked, coverage)->str`.

- [ ] **Step 1: Failing test**
```python
def test_leaderboard():
    from facultyfolio import render, rank, config
    lst = rank.ranked_list(config.CS_ORG_ID); cov = rank.coverage(config.CS_ORG_ID)
    html = render.render_leaderboard("Computer Science", lst, cov)
    assert "Ranked among 39 of 57 faculty with Google Scholar data" in html
    assert "by total citations" in html
    assert '../p/' in html            # rows link to profiles
```
- [ ] **Step 2: Run — expect FAIL.** [ ] **Step 3: Implement** using the shared shell. [ ] **Step 4: PASS.**
- [ ] **Step 5: Commit** `feat(facultyfolio): CS leaderboard page with coverage denominator`

---

### Task 10: `build.py` — orchestrate + idempotency [spec §11]

**Files:** Create `facultyfolio/build.py`, `scripts/build_facultyfolio.py` (runner); Test `facultyfolio/tests/test_build.py`

**Interfaces:**
- Produces: `build_one(slug, out_root)`, `build_all(out_root)` (skips `suppressed`), `main()`.

- [ ] **Step 1: Failing tests**
```python
def test_build_koutis(tmp_path):
    from facultyfolio import build
    build.build_one("ikoutis", tmp_path)
    assert (tmp_path/"p"/"ikoutis.html").exists()
def test_idempotent(tmp_path):
    from facultyfolio import build
    build.build_all(tmp_path); import hashlib
    h1 = {p.name: hashlib.md5(p.read_bytes()).hexdigest() for p in (tmp_path/"p").glob("*.html")}
    build.build_all(tmp_path)
    h2 = {p.name: hashlib.md5(p.read_bytes()).hexdigest() for p in (tmp_path/"p").glob("*.html")}
    assert h1 == h2      # byte-identical
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement:** `build_one` = dict→photo→render→write `p/<slug>.html`; `build_all` = loop `cs_faculty_slugs()` (minus suppressed) + `render_leaderboard`→`cs/index.html` + `copy_assets`. Deterministic ordering.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** `feat(facultyfolio): build orchestrator (idempotent) + runner`

---

## Checkpoint before fan-out (owner + Fable gates) [spec §12 pre-fan-out]

After Task 10 builds Koutis: **render Koutis, diff against `docs/samplepages/koutis.html`, show the owner.** Then the three Fable QA gates before generating all 57: (1) thinnest no-Scholar+no-areas page reads dignified; (2) eyeball all-CS teaching output for shouty fragments; (3) owner confirms "Active since" wording. Only then `build_all`.

## Self-review notes
- Spec coverage: every spec §(1–12) maps to a task (config→§1/§10; format→§7; chart→§6; db→§2/§3; rank→§8; render+templates→§4/§5; photos→§4; assets→§5; leaderboard→§9; build→§11; §12 tests distributed + checkpoint). ✓
- Types consistent across tasks: `get_faculty` dict shape consumed by render/build; `render_profile`/`render_leaderboard`/`ranked_list`/`coverage` names stable. ✓
- No placeholders except the deliberately-abbreviated template HTML (Task 6/9), which points to the reference file as the literal source. ✓
