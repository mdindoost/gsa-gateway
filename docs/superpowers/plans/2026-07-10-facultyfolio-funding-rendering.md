# FacultyFolio Funding Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render each faculty member's NSF+NIH research funding on their FacultyFolio profile, plus one aggregate funding rollup line on department leaderboards and college/root hubs.

**Architecture:** Rendering-only, on top of the `attrs.funding.{nsf,nih}` bags already in the KG (one small data-layer prerequisite, Task 0, captures NIH `appl_id` for record links). A `render.funding_view` builds the profile section view-model; `rank.funding_rollup` aggregates per org subtree; `build.py` wires the rollup through the existing `render_leaderboard`/`render_hub`. Reuses the existing `.pub` row grammar and `format` helpers.

**Tech Stack:** Python 3.11, Jinja2 templates, sqlite3 (read-only via `db.connect`), pytest. Static-site generator (`facultyfolio/`).

## Global Constraints

- **NSF and NIH shown separately, never summed into one figure** — anywhere, including the rollup.
- **Funding is federal NSF+NIH only** — provenance line bounds the claim; never "total funding".
- **Funding appears only** in the profile `funding` section and the aggregate `.rollup` line — **never** per-person on the hero, `_glance.html`, `_person_card.html`, or a leaderboard column.
- **Titles verbatim** — no case-fixing or prefix-stripping (crawl hard line).
- Every funding row links to its government record (NSF `showAward`, NIH `project-details/{appl_id}`).
- Money: exact comma-grouped `<$1M` (`$327,808`), compact `$X.XM` `>=$1M` (`$4.08M`); **group summary lines and `money_exact` always exact**.
- Active: NSF parsed `exp` date `>= today`; NIH `fy_last >=` federal FY (`year+1 if month>=10 else year`).
- Tests assert on `$` + class names, **never** the token "NSF" (award titles contain it).
- Gated live writes take `hardened_backup` + `--commit` (Task 0 only).

---

### Task 0: Capture NIH `appl_id` in the enrichment tool

**Files:**
- Modify: `scripts/funding_enrich.py` (the `nih_match` function — `include_fields` + the per-core loop)

**Interfaces:**
- Produces: `attrs.funding.nih.projects[].appl_id` (int, the latest fiscal-year row's appl_id per core project) for use by Task 3's NIH link.

- [ ] **Step 1: Add `ApplId` to the NIH request and store the latest-FY appl_id per core.**

In `scripts/funding_enrich.py`, in `nih_match`, change the `include_fields` list to add `"ApplId"`:

```python
        "include_fields": ["CoreProjectNum", "ProjectTitle", "PrincipalInvestigators",
                           "AwardAmount", "FiscalYear", "ApplId"],
```

Then make TWO surgical additive edits to the per-project loop that builds `cores` — do **not** rewrite the whole loop body (the `for pi in mine … c["pids"].add(...)` homonym-gate lines and the `if is_contact: c["role"] = "contact"` promotion line MUST stay unchanged).

Edit (a): add two keys to the `cores.setdefault(...)` default dict:

```python
        c = cores.setdefault(core, {"total": 0, "title": p.get("project_title"),
                                    "role": "contact" if is_contact else "co_pi",
                                    "fys": set(), "pids": set(),
                                    "appl_id": None, "appl_fy": -1})
```

Edit (b): replace ONLY the two-line fiscal-year block

```python
        if p.get("fiscal_year"):
            c["fys"].add(int(p["fiscal_year"]))
```

with a version that also records the appl_id of the highest fiscal year (everything after it — the `for pi in mine` loop and the `if is_contact` promotion — is left exactly as-is):

```python
        if p.get("fiscal_year"):
            fy = int(p["fiscal_year"])
            c["fys"].add(fy)
            if fy > c["appl_fy"] and p.get("appl_id"):
                c["appl_fy"] = fy
                c["appl_id"] = p["appl_id"]
```

And add `appl_id` to the emitted project dict (the `projects = [...]` comprehension):

```python
    projects = [{"core": k, "title": c["title"], "total": c["total"], "role": c["role"],
                 "fy_first": min(c["fys"]) if c["fys"] else None,
                 "fy_last": max(c["fys"]) if c["fys"] else None,
                 "appl_id": c["appl_id"]}
                for k, c in cores.items()]
```

- [ ] **Step 2: Dry-run to confirm appl_id is captured.**

Run: `python3 scripts/funding_enrich.py --org ywcc --source nih`
Expected: prints `Wei, Zhi` and `Yehoshua Perl` funded; DRY RUN summary. No error.

- [ ] **Step 3: Verify appl_id would be written (spot check via a tiny script).**

Run:
```bash
python3 - <<'PY'
import sys; sys.path.insert(0,'.')
from scripts.funding_enrich import nih_match
m = nih_match("Wei, Zhi", None, 25)
print([{k:p[k] for k in ('core','appl_id','fy_last')} for p in m['bag']['projects']])
PY
```
Expected: each project shows an integer `appl_id` (e.g. `R35GM158529` → `11378084`) and its `fy_last`.

- [ ] **Step 4: Gated live re-run (adds appl_id to the 2 NIH people; idempotent).**

Run: `python3 scripts/funding_enrich.py --org ywcc --source nih --commit`
Expected: `hardened_backup taken.` then `COMMITTED 2 node updates to gsa_gateway.db`.

- [ ] **Step 5: Confirm live data now has appl_id.**

Run:
```bash
python3 - <<'PY'
import sqlite3, json
c=sqlite3.connect('file:gsa_gateway.db?mode=ro',uri=True)
a=json.loads(c.execute("SELECT attrs FROM nodes WHERE name='Wei, Zhi'").fetchone()[0])
print([p.get('appl_id') for p in a['funding']['nih']['projects']])
PY
```
Expected: a list of two integers (no `None`).

- [ ] **Step 6: Commit.**

```bash
git add scripts/funding_enrich.py
git commit -m "feat(funding): capture NIH appl_id for per-project RePORTER links"
```

---

### Task 1: Money + date formatting helpers

**Files:**
- Modify: `facultyfolio/format.py` (add functions; `commafy` already exists at line 62)
- Test: `facultyfolio/tests/test_format_money.py` (create)

**Interfaces:**
- Produces: `money(n)`, `money_exact(n)`, `date_long(iso)`, `month_year(iso)` — used by Tasks 3 and 6.

- [ ] **Step 1: Write the failing test.**

Create `facultyfolio/tests/test_format_money.py`:

```python
from facultyfolio import format as F


def test_money_exact_under_1m():
    assert F.money(327808) == "$327,808"


def test_money_compact_at_and_over_1m():
    assert F.money(4078362) == "$4.08M"
    assert F.money(1653383) == "$1.65M"
    assert F.money(37401075) == "$37.40M"


def test_money_exact_always_commas():
    assert F.money_exact(1653383) == "$1,653,383"
    assert F.money_exact(0) == "$0"


def test_money_none_is_zero():
    assert F.money(None) == "$0"


def test_date_long_and_month_year():
    assert F.date_long("2026-07-10") == "Jul 10, 2026"
    assert F.month_year("2026-07-10") == "Jul 2026"
```

- [ ] **Step 2: Run to verify it fails.**

Run: `python3 -m pytest facultyfolio/tests/test_format_money.py -q`
Expected: FAIL with `AttributeError: module 'facultyfolio.format' has no attribute 'money'`.

- [ ] **Step 3: Implement the helpers.**

Append to `facultyfolio/format.py`:

```python
_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def money(n) -> str:
    """Exact comma-grouped below $1M; compact $X.XM at or above $1M."""
    n = int(n or 0)
    if n >= 1_000_000:
        return f"${n / 1_000_000:.2f}M"
    return f"${commafy(n)}"


def money_exact(n) -> str:
    """Always exact, comma-grouped (for group summary lines)."""
    return f"${commafy(int(n or 0))}"


def date_long(iso: str) -> str:
    """'2026-07-10' -> 'Jul 10, 2026'."""
    y, m, d = iso.split("-")
    return f"{_MONTHS[int(m)]} {int(d)}, {y}"


def month_year(iso: str) -> str:
    """'2026-07-10' -> 'Jul 2026'."""
    y, m, _ = iso.split("-")
    return f"{_MONTHS[int(m)]} {y}"
```

- [ ] **Step 4: Run to verify it passes.**

Run: `python3 -m pytest facultyfolio/tests/test_format_money.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit.**

```bash
git add facultyfolio/format.py facultyfolio/tests/test_format_money.py
git commit -m "feat(facultyfolio): money + date format helpers for funding"
```

---

### Task 2: Expose `funding` from `db.get_faculty`

**Files:**
- Modify: `facultyfolio/db.py` (the `get_faculty` return dict, ~line 199-222)
- Test: `facultyfolio/tests/test_db_funding.py` (create)

**Interfaces:**
- Produces: `get_faculty(slug)["funding"]` → the raw `attrs.funding` dict (`{}` when absent). Consumed by Task 3.

- [ ] **Step 1: Write the failing test.**

Create `facultyfolio/tests/test_db_funding.py`:

```python
from facultyfolio import db


def test_get_faculty_exposes_funding_for_funded_person():
    f = db.get_faculty("zhiwei")         # Zhi Wei (people.njit.edu/profile/zhiwei)
    assert "nsf" in f["funding"] or "nih" in f["funding"]
    assert f["funding"]["nih"]["njit_total"] == 1653383


def test_get_faculty_funding_empty_dict_when_absent():
    # A person with no funding still returns a dict, never KeyError.
    f = db.get_faculty("borcea")         # Cristian Borcea has NSF; pick a no-funding slug if needed
    assert isinstance(f["funding"], dict)
```

(If `borcea` turns out funded, the assertion still holds — `funding` is a dict either way. The point is the key exists and is a dict.)

- [ ] **Step 2: Run to verify it fails.**

Run: `python3 -m pytest facultyfolio/tests/test_db_funding.py -q`
Expected: FAIL with `KeyError: 'funding'`.

- [ ] **Step 3: Add the field to the return dict.**

In `facultyfolio/db.py`, `get_faculty`, the returned dict (where `attrs` is already parsed in scope), add one line next to `"scholar": scholar,`:

```python
            "scholar": scholar,
            "funding": attrs.get("funding") or {},
```

- [ ] **Step 4: Run to verify it passes.**

Run: `python3 -m pytest facultyfolio/tests/test_db_funding.py -q`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add facultyfolio/db.py facultyfolio/tests/test_db_funding.py
git commit -m "feat(facultyfolio): get_faculty exposes attrs.funding"
```

---

### Task 3: `render.funding_view` — the profile section view-model

**Files:**
- Modify: `facultyfolio/render.py` (add `funding_view` + module constants + a date-injectable helper)
- Test: `facultyfolio/tests/test_funding_view.py` (create)

**Interfaces:**
- Consumes: a faculty dict with `funding` (Task 2), `format.money/money_exact/date_long` (Task 1).
- Produces: `funding_view(f, today=None)` → `{"provenance": str, "groups": [group]}` or `None`.
  `group = {"agency": "NSF awards"|"NIH projects", "summary": str, "rows": [row]}`.
  `row = {"amount": str, "unit": str, "title": str, "url": str|None, "meta": str, "years": str, "active": bool, "copi": bool}`.

- [ ] **Step 1: Write the failing tests.**

Create `facultyfolio/tests/test_funding_view.py`:

```python
import datetime
from facultyfolio import render

TODAY = datetime.date(2026, 7, 10)   # deterministic

WEI = {"funding": {
    "nsf": {"updated_at": "2026-07-10", "njit_total": 327808, "awards": [
        {"id": "1659472", "title": "REU Site: X", "start": "05/01/2017",
         "exp": "04/30/2022", "obligated": 327808, "at_njit": True}]},
    "nih": {"updated_at": "2026-07-10", "njit_total": 1653383, "projects": [
        {"core": "R15HG012087", "title": "Deep Learning", "total": 900883,
         "role": "contact", "fy_first": 2021, "fy_last": 2024, "appl_id": 10974530},
        {"core": "R35GM158529", "title": "Single-cell Omics", "total": 752500,
         "role": "contact", "fy_first": 2025, "fy_last": 2026, "appl_id": 11378084}]}}}


def test_both_groups_present_and_ordered():
    v = render.funding_view(WEI, today=TODAY)
    assert [g["agency"] for g in v["groups"]] == ["NSF awards", "NIH projects"]
    nih = v["groups"][1]
    # recency-first: FY2025-2026 row before FY2021-2024
    assert nih["rows"][0]["years"] == "FY2025 – FY2026"
    assert nih["rows"][1]["years"] == "FY2021 – FY2024"


def test_summaries_and_units():
    v = render.funding_view(WEI, today=TODAY)
    nsf, nih = v["groups"]
    assert nsf["summary"] == "$327,808 obligated · 1 award"
    assert nih["summary"] == "$1,653,383 project costs · 2 projects (as contact PI)"
    assert nsf["rows"][0]["unit"] == "obligated"
    assert nih["rows"][0]["unit"] == "costs"


def test_active_chip_rules():
    v = render.funding_view(WEI, today=TODAY)
    nsf, nih = v["groups"]
    assert nsf["rows"][0]["active"] is False          # exp 2022 < today
    assert nih["rows"][0]["active"] is True            # fy_last 2026 >= FY2026
    assert nih["rows"][1]["active"] is False           # fy_last 2024


def test_links_and_provenance():
    v = render.funding_view(WEI, today=TODAY)
    assert v["groups"][0]["rows"][0]["url"] == "https://www.nsf.gov/awardsearch/showAward?AWD_ID=1659472"
    assert v["groups"][1]["rows"][0]["url"] == "https://reporter.nih.gov/project-details/11378084"
    assert v["provenance"] == "From NSF and NIH public award records · as of Jul 10, 2026"


def test_nsf_only_omits_nih_group():
    f = {"funding": {"nsf": WEI["funding"]["nsf"]}}
    v = render.funding_view(f, today=TODAY)
    assert [g["agency"] for g in v["groups"]] == ["NSF awards"]
    assert "NSF public award records" in v["provenance"]


def test_no_funding_returns_none():
    assert render.funding_view({"funding": {}}, today=TODAY) is None
    assert render.funding_view({}, today=TODAY) is None


def test_prior_institution_nsf_excluded():
    f = {"funding": {"nsf": {"updated_at": "2026-07-10", "njit_total": 0, "awards": [
        {"id": "1", "title": "Old", "start": "01/01/2010", "exp": "01/01/2014",
         "obligated": 500000, "at_njit": False}]}}}
    assert render.funding_view(f, today=TODAY) is None    # no at_njit rows -> no group -> None


def test_copi_only_summary_variant():
    f = {"funding": {"nih": {"updated_at": "2026-07-10", "njit_total": 0, "projects": [
        {"core": "U54X", "title": "Center", "total": 2400000, "role": "co_pi",
         "fy_first": 2023, "fy_last": 2028, "appl_id": 999}]}}}
    v = render.funding_view(f, today=TODAY)
    g = v["groups"][0]
    assert g["summary"] == "co-investigator on 1 project"
    assert g["rows"][0]["copi"] is True


def test_dollar_formatting_compact_and_exact():
    f = {"funding": {"nsf": {"updated_at": "2026-07-10", "njit_total": 9076163, "awards": [
        {"id": "a", "title": "T", "start": "08/01/2021", "exp": "07/31/2027",
         "obligated": 4078362, "at_njit": True}]}}}
    row = render.funding_view(f, today=TODAY)["groups"][0]["rows"][0]
    assert row["amount"] == "$4.08M"
    assert row["active"] is True                        # exp 2027 >= today
```

- [ ] **Step 2: Run to verify it fails.**

Run: `python3 -m pytest facultyfolio/tests/test_funding_view.py -q`
Expected: FAIL with `AttributeError: module 'facultyfolio.render' has no attribute 'funding_view'`.

- [ ] **Step 3: Implement `funding_view`.**

At the top of `facultyfolio/render.py`, ensure `import datetime` is present (add if missing), and that `format` is imported as `F` (it already is — used as `F.initials`). Add these module-level constants and the function (place near the other view-model helpers, e.g. after `_pub`):

```python
_NSF_LINK = "https://www.nsf.gov/awardsearch/showAward?AWD_ID={}"
_NIH_LINK = "https://reporter.nih.gov/project-details/{}"


def _exp_date(mdy):
    try:
        return datetime.datetime.strptime(mdy, "%m/%d/%Y").date()
    except (ValueError, TypeError):
        return None


def _year(mdy):
    d = _exp_date(mdy)
    return d.year if d else "—"


def funding_view(f: dict, today: datetime.date = None) -> dict | None:
    """Profile 'Research funding' view-model. NSF group then NIH group, each
    summary + recency-ordered rows. None when there are no contributing rows."""
    fund = f.get("funding") or {}
    today = today or datetime.date.today()
    fy_now = today.year + 1 if today.month >= 10 else today.year
    groups, updated = [], []

    nsf = fund.get("nsf")
    if nsf:
        rows = [a for a in nsf.get("awards", []) if a.get("at_njit")]
        if rows:
            updated.append(nsf["updated_at"])
            rows = sorted(rows, key=lambda a: (_exp_date(a["exp"]) or datetime.date.min,
                                               a["obligated"]), reverse=True)
            n = len(rows)
            groups.append({
                "agency": "NSF awards",
                "summary": f'{F.money_exact(nsf["njit_total"])} obligated · {n} award{"" if n == 1 else "s"}',
                "rows": [{
                    "amount": F.money(a["obligated"]), "unit": "obligated",
                    "title": a["title"], "url": _NSF_LINK.format(a["id"]),
                    "meta": f'NSF {a["id"]}',
                    "years": f'{_year(a["start"])} – {_year(a["exp"])}',
                    "active": bool(_exp_date(a["exp"]) and _exp_date(a["exp"]) >= today),
                    "copi": False,
                } for a in rows],
            })

    nih = fund.get("nih")
    if nih:
        projects = nih.get("projects", [])
        contact = [p for p in projects if p.get("role") == "contact"]
        copi = [p for p in projects if p.get("role") == "co_pi"]
        if contact or copi:
            updated.append(nih["updated_at"])
            key = lambda p: (p.get("fy_last") or 0, p.get("total") or 0)
            ordered = sorted(contact, key=key, reverse=True) + sorted(copi, key=key, reverse=True)
            if contact:
                nc = len(contact)
                summary = (f'{F.money_exact(nih["njit_total"])} project costs · '
                           f'{nc} project{"" if nc == 1 else "s"} (as contact PI)')
            else:
                ncp = len(copi)
                summary = f'co-investigator on {ncp} project{"" if ncp == 1 else "s"}'
            groups.append({
                "agency": "NIH projects", "summary": summary,
                "rows": [{
                    "amount": F.money(p["total"]),
                    "unit": "costs" if p["role"] == "contact" else "project",
                    "title": p["title"],
                    "url": _NIH_LINK.format(p["appl_id"]) if p.get("appl_id") else None,
                    "meta": f'NIH {p["core"]}',
                    "years": f'FY{p["fy_first"]} – FY{p["fy_last"]}' if p.get("fy_first") else "—",
                    "active": bool(isinstance(p.get("fy_last"), int) and p["fy_last"] >= fy_now),
                    "copi": p["role"] == "co_pi",
                } for p in ordered],
            })

    if not groups:
        return None
    present = [g["agency"].split()[0] for g in groups]     # ["NSF"], ["NIH"], or both
    src = " and ".join(present)
    as_of = min(u for u in updated if u)                   # YYYY-MM-DD sorts chronologically
    return {"groups": groups,
            "provenance": f"From {src} public award records · as of {F.date_long(as_of)}"}
```

- [ ] **Step 4: Run to verify it passes.**

Run: `python3 -m pytest facultyfolio/tests/test_funding_view.py -q`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit.**

```bash
git add facultyfolio/render.py facultyfolio/tests/test_funding_view.py
git commit -m "feat(facultyfolio): funding_view profile section view-model"
```

---

### Task 4: Profile template section + CSS + wire into `render_profile`

**Files:**
- Modify: `facultyfolio/render.py` (`render_profile` ctx — add `"funding"`)
- Modify: `facultyfolio/templates/profile.html` (new section after `#pubs`)
- Modify: `facultyfolio/assets/style.css` (`.fund-*`, `.chip.active`, `.chip.copi`)
- Test: `facultyfolio/tests/test_profile_funding_render.py` (create)

**Interfaces:**
- Consumes: `funding_view` (Task 3).
- Produces: rendered profile HTML containing the funding section when funded.

- [ ] **Step 1: Write the failing test.**

Create `facultyfolio/tests/test_profile_funding_render.py`:

```python
from facultyfolio import db, render


def test_funded_profile_has_funding_section():
    f = db.get_faculty("zhiwei")
    html = render.render_profile(f)
    assert 'class="eyebrow">Sponsored research' in html
    assert "Research funding" in html
    assert "obligated" in html and "costs" in html
    assert "reporter.nih.gov/project-details/" in html


def test_unfunded_profile_has_no_funding_section():
    # find a person with no funding; Selected work etc. still render
    f = db.get_faculty("zhiwei")
    f = dict(f); f["funding"] = {}
    html = render.render_profile(f)
    assert "Research funding" not in html


def test_funded_but_no_scholar_still_renders_funding():
    # B2 guard: funding must render even when the Scholar/pubs block is absent.
    f = db.get_faculty("calvin")         # funded, no Scholar block (verify slug at build time)
    if not f.get("funding"):
        import pytest; pytest.skip("fixture slug not funded; pick another no-Scholar funded slug")
    f = dict(f); f["scholar"] = None
    html = render.render_profile(f)
    assert "Research funding" in html
```

- [ ] **Step 2: Run to verify it fails.**

Run: `python3 -m pytest facultyfolio/tests/test_profile_funding_render.py -q`
Expected: FAIL (no "Research funding" in output).

- [ ] **Step 3: Add `funding` to the profile ctx.**

In `facultyfolio/render.py`, `render_profile`, add to the `ctx` dict (near `"scholar": sch,`):

```python
        "funding": funding_view(f),
```

- [ ] **Step 4: Add the template section.**

In `facultyfolio/templates/profile.html`, insert the funding section **between** the `{% endif %}` that closes the Publications block (line 141) and the Recognition `<section>` (line 143). **Do NOT put it inside the `{% if scholar and (top_cited or newest) %}` … `{% endif %}` block** — 3 funded faculty have no Scholar (`calvin`, `sohna`, `ss797`) and their funding would silently vanish. The section is guarded by its own `{% if funding %}`:

```html
      {% if funding %}
      <section id="funding">
        <p class="eyebrow">Sponsored research</p>
        <h2>Research funding</h2>
        <div class="rule"></div>
        <p class="fund-note">{{ funding.provenance }}</p>
        {% for g in funding.groups %}
        <div class="fund-group">
          <div class="fund-sum"><span class="ag">{{ g.agency }}</span><span class="amt">{{ g.summary }}</span></div>
          {% for r in g.rows %}
          <div class="fund-row{% if r.copi %} copi{% endif %}">
            <div class="fund-cite"><span class="n">{{ r.amount }}</span><span class="l">{{ r.unit }}</span></div>
            <div class="fund-main">
              {% if r.url %}<a class="fund-t" href="{{ r.url }}">{{ r.title }}</a>{% else %}<span class="fund-t">{{ r.title }}</span>{% endif %}
              <div class="fund-meta"><span>{{ r.meta }}</span><span class="sep">·</span><span>{{ r.years }}</span>{% if r.copi %}<span class="chip copi">co-PI</span>{% endif %}{% if r.active %}<span class="chip active">Active</span>{% endif %}</div>
            </div>
          </div>
          {% endfor %}
        </div>
        {% endfor %}
      </section>
      {% endif %}
```

- [ ] **Step 5: Add the CSS.**

Append to `facultyfolio/assets/style.css` (derives from the existing `.pub` grammar):

```css
  /* research funding */
  .fund-note{font-family:var(--f-mono); font-size:11px; color:var(--faint); margin:0 0 20px;}
  .fund-group{margin:0 0 22px;} .fund-group:last-child{margin-bottom:0;}
  .fund-sum{display:flex; justify-content:space-between; align-items:baseline; flex-wrap:wrap; gap:6px; margin:0 0 6px; padding-bottom:8px; border-bottom:1px solid var(--hair);}
  .fund-sum .ag{font-size:15px; font-weight:600; color:var(--ink);}
  .fund-sum .amt{font-family:var(--f-mono); font-size:12px; color:var(--mute); font-variant-numeric:tabular-nums;}
  .fund-row{display:flex; gap:18px; padding:14px 0; border-bottom:1px solid var(--hair-2); align-items:baseline;}
  .fund-row:last-child{border-bottom:none;}
  .fund-cite{width:92px; flex-shrink:0; text-align:right;}
  .fund-cite .n{font-family:var(--f-display); font-size:18px; font-weight:600; color:var(--ink); display:block; line-height:1.05; font-variant-numeric:tabular-nums;}
  .fund-cite .l{font-family:var(--f-mono); font-size:10px; text-transform:uppercase; letter-spacing:.04em; color:var(--faint);}
  .fund-row.copi .fund-cite .n{color:var(--faint);}
  .fund-main{flex:1; min-width:0;}
  .fund-t{font-size:15px; font-weight:500; color:var(--ink); text-decoration:none; line-height:1.4; display:block; margin-bottom:4px;}
  a.fund-t:hover{color:var(--accent);}
  .fund-meta{font-family:var(--f-mono); font-size:11.5px; color:var(--mute); display:flex; flex-wrap:wrap; align-items:center; gap:8px;}
  .chip{font-family:var(--f-mono); font-size:9.5px; letter-spacing:.05em; text-transform:uppercase; padding:2px 7px; border-radius:20px; line-height:1.4;}
  .chip.active{background:var(--pos-soft,#e6f4ec); color:var(--pos);}
  .chip.copi{background:var(--hair); color:var(--mute);}
  @media (max-width:540px){ .fund-cite{width:78px;} .fund-row{gap:12px;} }
```

(If `--pos-soft` is not defined in `:root`, the `#e6f4ec` fallback in the declaration covers it.)

- [ ] **Step 6: Run to verify it passes.**

Run: `python3 -m pytest facultyfolio/tests/test_profile_funding_render.py -q`
Expected: PASS.

- [ ] **Step 7: Commit.**

```bash
git add facultyfolio/render.py facultyfolio/templates/profile.html facultyfolio/assets/style.css facultyfolio/tests/test_profile_funding_render.py
git commit -m "feat(facultyfolio): render Research funding section on profiles"
```

---

### Task 5: `rank.funding_rollup` — org-subtree aggregate

**Files:**
- Modify: `facultyfolio/rank.py` (add `funding_rollup`; `_members`/`connect`/`config`/`json` are in scope)
- Test: `facultyfolio/tests/test_funding_rollup.py` (create)

**Interfaces:**
- Produces: `funding_rollup(org_ids: list[int]) -> dict | None` →
  `{"nsf": int, "nih": int, "n_funded": int, "as_of": "YYYY-MM-DD"}` or `None` when the subtree has no funding. Consumed by Task 6.

- [ ] **Step 1: Write the failing test.**

Create `facultyfolio/tests/test_funding_rollup.py`:

```python
from facultyfolio import rank, db


def test_ywcc_rollup_totals():
    ywcc = db.org_node_by_slug("ywcc")
    org_ids = [d["node_id"] for d in db.dept_orgs_of_college(ywcc)] + [ywcc]
    r = rank.funding_rollup(org_ids)
    assert r["nsf"] == 37401075
    assert r["nih"] == 6076611
    assert r["n_funded"] == 36


def test_data_science_has_no_nih():
    ds = db.org_node_by_slug("data-science")
    r = rank.funding_rollup([ds])
    assert r["nih"] == 0
    assert r["nsf"] > 0


def test_empty_subtree_returns_none():
    # a real org with no funded faculty -> None (use an org id unlikely to have funding)
    assert rank.funding_rollup([]) is None
```

- [ ] **Step 2: Run to verify it fails.**

Run: `python3 -m pytest facultyfolio/tests/test_funding_rollup.py -q`
Expected: FAIL with `AttributeError: ... has no attribute 'funding_rollup'`.

- [ ] **Step 3: Implement `funding_rollup`.**

Add to `facultyfolio/rank.py` (uses `connect`, `config`, `json` already imported in the module):

```python
def funding_rollup(org_ids):
    """Aggregate NSF+NIH funding across the given org node ids' home faculty.
    Dedup by person node id (a dup-home person counted once). Returns
    {nsf, nih, n_funded, as_of} or None when nothing is funded."""
    conn = connect()
    seen = {}
    try:
        for oid in org_ids:
            for r in conn.execute(
                """SELECT n.id AS id, n.key AS key, n.attrs AS attrs FROM nodes n
                   JOIN edges e ON e.src_id=n.id
                   WHERE n.type='Person' AND n.is_active=1
                     AND e.type='has_role' AND e.category='faculty'
                     AND e.dst_id=? AND e.is_active=1""", (oid,)):
                if r["id"] in seen or r["key"].split("/")[-1] in config.SUPPRESSED:
                    continue
                fund = (json.loads(r["attrs"]) if r["attrs"] else {}).get("funding") or {}
                nsf = int((fund.get("nsf") or {}).get("njit_total") or 0)
                nih = int((fund.get("nih") or {}).get("njit_total") or 0)
                dates = [b["updated_at"] for b in (fund.get("nsf"), fund.get("nih"))
                         if b and b.get("updated_at")]
                seen[r["id"]] = (nsf, nih, dates)
    finally:
        conn.close()
    nsf_t = sum(v[0] for v in seen.values())
    nih_t = sum(v[1] for v in seen.values())
    if nsf_t == 0 and nih_t == 0:
        return None
    n_funded = sum(1 for v in seen.values() if v[0] > 0 or v[1] > 0)
    all_dates = [d for v in seen.values() if v[0] > 0 or v[1] > 0 for d in v[2]]  # counted bags only
    return {"nsf": nsf_t, "nih": nih_t, "n_funded": n_funded,
            "as_of": min(all_dates) if all_dates else None}
```

- [ ] **Step 4: Run to verify it passes.**

Run: `python3 -m pytest facultyfolio/tests/test_funding_rollup.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit.**

```bash
git add facultyfolio/rank.py facultyfolio/tests/test_funding_rollup.py
git commit -m "feat(facultyfolio): funding_rollup org-subtree aggregate"
```

---

### Task 6: Wire the rollup into hubs + leaderboards

**Files:**
- Modify: `facultyfolio/render.py` (`render_hub` + `render_leaderboard` signatures + a `_rollup_view` helper)
- Modify: `facultyfolio/build.py` (`build_dept`, `build_college_hub`, `build_njit_hub`)
- Modify: `facultyfolio/templates/hub.html` and `facultyfolio/templates/leaderboard.html`
- Modify: `facultyfolio/assets/style.css` (`.rollup`)
- Test: `facultyfolio/tests/test_rollup_render.py` (create)

**Interfaces:**
- Consumes: `funding_rollup` raw dict (Task 5), `format.money/month_year` (Task 1).
- Produces: `render_hub(..., funding_rollup=None)` and `render_leaderboard(..., funding_rollup=None)` render a `.rollup` line when the raw dict is non-empty.

- [ ] **Step 1: Write the failing test.**

Create `facultyfolio/tests/test_rollup_render.py`:

```python
from facultyfolio import render


ROLLUP = {"nsf": 37401075, "nih": 6076611, "n_funded": 36, "as_of": "2026-07-10"}
ROLLUP_NSF_ONLY = {"nsf": 6958743, "nih": 0, "n_funded": 7, "as_of": "2026-07-10"}


def test_rollup_view_both_agencies():
    v = render._rollup_view(ROLLUP)
    assert v["parts"] == [("$37.40M", "NSF"), ("$6.08M", "NIH")]
    assert v["n"] == 36
    assert v["as_of"] == "Jul 2026"


def test_rollup_view_omits_zero_agency():
    v = render._rollup_view(ROLLUP_NSF_ONLY)
    assert v["parts"] == [("$6.96M", "NSF")]


def test_rollup_view_none():
    assert render._rollup_view(None) is None
    assert render._rollup_view({"nsf": 0, "nih": 0, "n_funded": 0, "as_of": None}) is None
```

- [ ] **Step 2: Run to verify it fails.**

Run: `python3 -m pytest facultyfolio/tests/test_rollup_render.py -q`
Expected: FAIL with `AttributeError: ... '_rollup_view'`.

- [ ] **Step 3: Add `_rollup_view` and thread the param through both render functions.**

In `facultyfolio/render.py`, add the helper:

```python
def _rollup_view(r: dict | None) -> dict | None:
    """Raw funding_rollup dict -> template-ready {parts:[($str, agency)], n, as_of}."""
    if not r or (not r.get("nsf") and not r.get("nih")):
        return None
    parts = []
    if r["nsf"]:
        parts.append((F.money(r["nsf"]), "NSF"))
    if r["nih"]:
        parts.append((F.money(r["nih"]), "NIH"))
    return {"parts": parts, "n": r["n_funded"],
            "as_of": F.month_year(r["as_of"]) if r.get("as_of") else ""}
```

In `render_hub`, add `funding_rollup=None` to the signature and to the rendered ctx. Its signature is:
```python
def render_hub(title: str, cards: list, *, eyebrow: str, asset_root: str,
               ... stats: dict = None, leadership: dict = None) -> str:
```
Add `funding_rollup: dict = None` to the keyword args, and where it builds the template kwargs add `funding_rollup=_rollup_view(funding_rollup)`.

In `render_leaderboard`, add `funding_rollup=None` to the signature and pass `funding_rollup=_rollup_view(funding_rollup)` into the template render call.

- [ ] **Step 4: Run the `_rollup_view` test to verify it passes.**

Run: `python3 -m pytest facultyfolio/tests/test_rollup_render.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Add the `.rollup` template block to both templates.**

In `facultyfolio/templates/hub.html`, under the existing rank-rollup/stats block, add:

```html
        {% if funding_rollup %}
        <div class="rollup"><span class="rk">Sponsored research</span>{% for amt, ag in funding_rollup.parts %}<span><b>{{ amt }}</b> {{ ag }}</span><span class="sep">·</span>{% endfor %}<span><b>{{ funding_rollup.n }}</b> funded faculty</span><span class="sep">·</span><span class="asof">as of {{ funding_rollup.as_of }}</span></div>
        {% endif %}
```

In `facultyfolio/templates/leaderboard.html`, immediately after the `{{ glance(stats) }}` call (leaderboard.html line 62; the `lb-glance` markup lives inside that macro / `_glance.html`), add the identical block. (In `hub.html` the anchor is the `{{ glance(stats) }}` at line 20 — put the block right after it, per Step 5.)

- [ ] **Step 6: Add `.rollup` CSS.**

Append to `facultyfolio/assets/style.css`:

```css
  .rollup{font-family:var(--f-mono); font-size:13px; color:var(--ink-2); background:var(--accent-soft); border-radius:10px; padding:12px 16px; margin:16px 0 0; display:flex; flex-wrap:wrap; gap:6px 12px; align-items:baseline;}
  .rollup .rk{color:var(--accent); text-transform:uppercase; letter-spacing:.1em; font-size:10.5px;}
  .rollup b{color:var(--ink); font-variant-numeric:tabular-nums;}
  .rollup .sep{color:var(--faint);} .rollup .asof{color:var(--faint);}
```

- [ ] **Step 7: Wire `build.py` (all three call sites).**

In `build_dept`, before the `render.render_leaderboard(...)` call add:
```python
    funding_rollup = rank.funding_rollup([org["node_id"]])
```
and pass `funding_rollup=funding_rollup` into `render.render_leaderboard(...)`.

In `build_college_hub`, before the `render.render_hub(...)` call add:
```python
    funding_rollup = rank.funding_rollup([d["node_id"] for d in depts] + [college_node])
```
and pass `funding_rollup=funding_rollup` into `render.render_hub(...)`.

In `build_njit_hub`, build the org-id list from the published colleges and pass it:
```python
    org_ids = []
    for slug in config.PUBLISHED_COLLEGES:
        node = db.org_node_by_slug(slug)
        org_ids += [d["node_id"] for d in db.dept_orgs_of_college(node)] + [node]
    funding_rollup = rank.funding_rollup(org_ids)
```
and pass `funding_rollup=funding_rollup` into `render.render_hub(...)`.

- [ ] **Step 8: Verify a real build renders the rollup on the YWCC hub + a dept leaderboard.**

Run:
```bash
python3 -m facultyfolio.build --college ywcc >/dev/null 2>&1
grep -o 'class="rollup".\{0,80\}' /home/md724/Faculty-Folio/ywcc/index.html | head -1
grep -c 'class="rollup"' /home/md724/Faculty-Folio/ywcc/computer-science/index.html
```
Expected: the YWCC hub line shows `Sponsored research … $37.40M NSF …`; the CS leaderboard grep prints `1`.

- [ ] **Step 9: Commit.**

```bash
git add facultyfolio/render.py facultyfolio/build.py facultyfolio/templates/hub.html facultyfolio/templates/leaderboard.html facultyfolio/assets/style.css facultyfolio/tests/test_rollup_render.py
git commit -m "feat(facultyfolio): funding rollup line on hubs + dept leaderboards"
```

---

### Task 7: Honest-labeling invariant + tripwire tests

**Files:**
- Test: `facultyfolio/tests/test_funding_invariants.py` (create)

**Interfaces:**
- Consumes: everything above. No production code — this task is the guardrail.

- [ ] **Step 1: Write the invariant + tripwire tests.**

Create `facultyfolio/tests/test_funding_invariants.py`:

```python
import json, os, re
from facultyfolio import db, render, config


def _funded_slugs():
    c = db.connect()
    rows = c.execute("SELECT key, attrs FROM nodes WHERE type='Person' AND is_active=1 "
                     "AND json_extract(attrs,'$.funding') IS NOT NULL").fetchall()
    c.close()
    return [(r["key"].split("/")[-1], json.loads(r["attrs"])) for r in rows]


def test_njit_total_equals_sum_of_contributing_rows():
    for slug, attrs in _funded_slugs():
        f = attrs.get("funding", {})
        if "nsf" in f:
            s = sum(a["obligated"] for a in f["nsf"]["awards"] if a.get("at_njit"))
            assert s == f["nsf"]["njit_total"], f"NSF drift for {slug}"
        if "nih" in f:
            s = sum(p["total"] for p in f["nih"]["projects"] if p.get("role") == "contact")
            assert s == f["nih"]["njit_total"], f"NIH drift for {slug}"


def test_no_dollars_or_fund_classes_on_person_card_or_hero():
    # person-card macro (leaderboard/hub rows) and the profile hero must carry no funding.
    f = db.get_faculty("zhiwei")            # a funded person
    html = render.render_profile(f)
    # the hero/aside is the left identity card; funding lives only in the #funding section.
    hero = html.split('id="funding"')[0]    # everything before the funding section
    assert "$" not in hero
    assert "fund-" not in hero
    assert "rollup" not in hero


# Every aggregate page: no per-person .fund- classes leak, and every '$' sits inside a .rollup.
AGGREGATE_PAGES = [
    "ywcc/computer-science/index.html",     # dept leaderboard
    "ywcc/index.html",                      # college hub
    "index.html",                           # NJIT root hub
]


def test_aggregate_pages_no_fund_classes_and_dollars_only_in_rollup():
    for rel in AGGREGATE_PAGES:
        out = os.path.join(config.OUT_ROOT, rel)
        assert os.path.exists(out), f"build the site first (Task 6/8): {rel}"
        html = open(out).read()
        assert "fund-" not in html, f"profile-only .fund- class leaked into {rel}"
        non_rollup = re.sub(r'<div class="rollup".*?</div>', "", html, flags=re.S)
        assert "$" not in non_rollup, f"a '$' appears outside the aggregate rollup in {rel}"
```

- [ ] **Step 2: Run to verify (build first if needed).**

Run (full build so the root `index.html` + all hubs exist):
```bash
python3 -m facultyfolio.build >/dev/null 2>&1
python3 -m pytest facultyfolio/tests/test_funding_invariants.py -q
```
Expected: PASS (3 passed).

- [ ] **Step 3: Run the whole suite.**

Run: `python3 -m pytest facultyfolio/tests/ -q`
Expected: all green (no regressions).

- [ ] **Step 4: Commit.**

```bash
git add facultyfolio/tests/test_funding_invariants.py
git commit -m "test(facultyfolio): funding honest-labeling invariants + njit_total tripwire"
```

---

### Task 8: Full rebuild + visual spot check + deploy

**Files:** none (build + deploy)

- [ ] **Step 1: Full rebuild.**

Run: `python3 -m facultyfolio.build`
Expected: `FacultyFolio: built N faculty ...`, exit 0.

- [ ] **Step 2: Spot-check the rendered pages against the approved mockup.**

Open in a browser (or grep) under `config.OUT_ROOT`: `p/zhiwei.html` (both groups), `p/oria.html` (NSF-only, large), `p/perl.html` (NIH), the YWCC hub `ywcc/index.html`, the root `index.html`, and a CS leaderboard `ywcc/computer-science/index.html`. Confirm: NSF above NIH, unit words, Active chips, one NSF + one NIH link resolve, rollup lines present.

- [ ] **Step 3: Deploy (owner-gated — confirm before pushing Pages).**

```bash
cd /home/md724/Faculty-Folio && git add -A && git commit -m "Profiles: NSF+NIH research funding section + hub/leaderboard rollup" && git push origin main
```

- [ ] **Step 4: Merge the feature branch to main + push source.**

```bash
cd /home/md724/gsa-gateway && git checkout main && git merge --no-ff feat/facultyfolio-funding-render -m "Merge: FacultyFolio funding rendering (NSF+NIH profile section + rollup)" && git push origin main
```

---

## Self-Review

- **Spec coverage:** Task 0 = appl_id; Tasks 1–4 = profile section (placement, groups, rows, dollar/unit, Active, co-PI, links, provenance, adaptive/absent); Task 5–6 = rollup on leaderboards + hubs (build.py wired, dedup, $0-omit, never-summed); Task 7 = honest-labeling invariants + tripwire; Task 8 = build/deploy. Deferred items (prior-institution footnote, OpenAlex funder breadth) intentionally absent. ✓
- **Placeholder scan:** every code step carries real code; no TBD/"handle edge cases". ✓
- **Type consistency:** `funding_view` returns `{groups, provenance}`; template reads `funding.groups[].{agency,summary,rows}` and `row.{amount,unit,title,url,meta,years,active,copi}` — matches. `funding_rollup` returns `{nsf,nih,n_funded,as_of}`; `_rollup_view` consumes those exact keys; template reads `funding_rollup.{parts,n,as_of}` — matches. `money/money_exact/date_long/month_year` defined in Task 1, used in Tasks 3/6. ✓
