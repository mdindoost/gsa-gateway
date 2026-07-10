# FacultyFolio Funding v2 (Awards & Counts, No Dollars) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the live dollar-based funding display with award **names** + activity **counts** — no dollar amounts, PI-led only (NSF lead-PI, NIH contact-PI; co-PIs dropped).

**Architecture:** Display + rollup rework only — the `attrs.funding` bags are untouched. `render.funding_view` and `rank.funding_rollup` change shape; templates + CSS + tests follow. The dollar helpers become dead and are removed.

**Tech Stack:** Python 3.11, Jinja2, sqlite3 (read-only via `db.connect`/`rank.connect`), pytest.

**Spec:** `docs/superpowers/specs/2026-07-10-facultyfolio-funding-v2-counts-design.md` (Fable-approved).

## Global Constraints

- **No `$` in generated funding scaffolding** (summaries, meta, ranges, rollup). Invariant tests assert this over the scaffolding but **exclude the verbatim `.fund-t` award title** (a future title could contain `$`; the verbatim hard line forbids stripping it).
- **No co-PIs anywhere.** NSF = `at_njit` awards (already lead-PI). NIH = `role == "contact"` only. Remove the co-PI rows, the `copi` chip, and the co-PI-only summary variant.
- **NSF and NIH counts shown separately, never summed.**
- **Funding appears only** in the profile `#funding` section and the aggregate `.rollup` line — never per-person on hero/glance/card/leaderboard-column.
- **Titles verbatim**, each linked to its government record.
- Role wording on every summary: **"as Principal Investigator"** (NSF) / **"as Contact PI"** (NIH).
- **Determinism:** `funding_view` and `funding_rollup` take an injectable `today` (default `datetime.date.today()`); count tests pin `today=datetime.date(2026,7,10)` (federal `fy_now=2026`).
- **Do NOT touch the `attrs.funding` data, `funding_enrich.py`, or the `njit_total` tripwire test** — bags stay; the number is simply never displayed.
- Live count targets (today=2026-07-10): CS **NSF 59 (14 active) · NIH 5 (1 active) · 23 funded**; YWCC **92 (25) · 5 (1) · 36**; data-science **17 (8) · 0 · 7**.

---

### Task 1: Rewrite `render.funding_view` (counts, no dollars, NIH contact-only)

**Files:**
- Modify: `facultyfolio/render.py` (`funding_view`)
- Test: `facultyfolio/tests/test_funding_view.py` (rewrite)

**Interfaces:**
- Produces: `funding_view(f, today=None) -> {"groups": [group], "provenance": str} | None`.
  `group = {"agency": "NSF"|"NIH", "summary": str, "rows": [row]}`.
  `row = {"title": str, "url": str|None, "meta": str, "years": str, "active": bool}` — **no** `amount`/`unit`/`copi`.

- [ ] **Step 1: Rewrite the test file.**

Replace `facultyfolio/tests/test_funding_view.py` entirely with:

```python
import datetime
from facultyfolio import render

TODAY = datetime.date(2026, 7, 10)

WEI = {"funding": {
    "nsf": {"updated_at": "2026-07-10", "njit_total": 327808, "awards": [
        {"id": "1659472", "title": "REU Site: X", "start": "05/01/2017",
         "exp": "04/30/2022", "obligated": 327808, "at_njit": True}]},
    "nih": {"updated_at": "2026-07-10", "njit_total": 1653383, "projects": [
        {"core": "R35GM158529", "title": "Single-cell Omics", "total": 752500,
         "role": "contact", "fy_first": 2025, "fy_last": 2026, "appl_id": 11378084},
        {"core": "R15HG012087", "title": "Deep Learning", "total": 900883,
         "role": "contact", "fy_first": 2021, "fy_last": 2024, "appl_id": 10974530}]}}}


def test_groups_are_nsf_then_nih_no_dollars():
    v = render.funding_view(WEI, today=TODAY)
    assert [g["agency"] for g in v["groups"]] == ["NSF", "NIH"]
    blob = repr(v)
    assert "$" not in blob                      # no dollars anywhere in the view-model
    # rows carry no amount/unit/copi keys
    for g in v["groups"]:
        for r in g["rows"]:
            assert set(r.keys()) == {"title", "url", "meta", "years", "active"}


def test_count_summaries_with_pi_wording():
    v = render.funding_view(WEI, today=TODAY)
    nsf, nih = v["groups"]
    assert nsf["summary"] == "1 award · as Principal Investigator"
    assert nih["summary"] == "2 projects · as Contact PI"


def test_nih_recency_order_and_active():
    v = render.funding_view(WEI, today=TODAY)
    nih = v["groups"][1]
    assert nih["rows"][0]["years"] == "FY2025 – FY2026"   # fy_last 2026 first
    assert nih["rows"][0]["active"] is True                # fy_last 2026 >= FY2026
    assert nih["rows"][1]["active"] is False               # fy_last 2024
    assert v["groups"][0]["rows"][0]["active"] is False    # NSF exp 2022 < today


def test_links_and_meta_and_provenance():
    v = render.funding_view(WEI, today=TODAY)
    assert v["groups"][0]["rows"][0]["url"] == "https://www.nsf.gov/awardsearch/showAward?AWD_ID=1659472"
    assert v["groups"][0]["rows"][0]["meta"] == "NSF 1659472"
    assert v["groups"][1]["rows"][0]["url"] == "https://reporter.nih.gov/project-details/11378084"
    assert v["provenance"] == "From NSF and NIH public award records · as of Jul 10, 2026"


def test_nih_co_pi_dropped_entirely():
    f = {"funding": {"nih": {"updated_at": "2026-07-10", "njit_total": 0, "projects": [
        {"core": "U54X", "title": "Center", "total": 2400000, "role": "co_pi",
         "fy_first": 2023, "fy_last": 2028, "appl_id": 999}]}}}
    # only co-PI projects -> no NIH group at all -> None
    assert render.funding_view(f, today=TODAY) is None


def test_nih_contact_kept_copi_filtered_out():
    f = {"funding": {"nih": {"updated_at": "2026-07-10", "njit_total": 500000, "projects": [
        {"core": "R01A", "title": "Contact one", "total": 500000, "role": "contact",
         "fy_first": 2022, "fy_last": 2025, "appl_id": 1},
        {"core": "U54B", "title": "CoPI one", "total": 2400000, "role": "co_pi",
         "fy_first": 2023, "fy_last": 2028, "appl_id": 2}]}}}
    v = render.funding_view(f, today=TODAY)
    g = v["groups"][0]
    assert g["summary"] == "1 project · as Contact PI"
    assert [r["meta"] for r in g["rows"]] == ["NIH R01A"]     # co-PI row absent


def test_nsf_only_and_none_cases():
    nsf_only = {"funding": {"nsf": WEI["funding"]["nsf"]}}
    assert [g["agency"] for g in render.funding_view(nsf_only, today=TODAY)["groups"]] == ["NSF"]
    assert "From NSF public award records" in render.funding_view(nsf_only, today=TODAY)["provenance"]
    assert render.funding_view({"funding": {}}, today=TODAY) is None
    assert render.funding_view({}, today=TODAY) is None


def test_prior_institution_nsf_excluded():
    f = {"funding": {"nsf": {"updated_at": "2026-07-10", "njit_total": 0, "awards": [
        {"id": "1", "title": "Old", "start": "01/01/2010", "exp": "01/01/2014",
         "obligated": 500000, "at_njit": False}]}}}
    assert render.funding_view(f, today=TODAY) is None       # no at_njit rows -> None


def test_nsf_multi_award_recency_order():
    f = {"funding": {"nsf": {"updated_at": "2026-07-10", "njit_total": 3, "awards": [
        {"id": "old", "title": "Old", "start": "01/01/2012", "exp": "01/01/2016", "obligated": 1, "at_njit": True},
        {"id": "new", "title": "New", "start": "08/01/2021", "exp": "07/31/2027", "obligated": 2, "at_njit": True}]}}}
    rows = render.funding_view(f, today=TODAY)["groups"][0]["rows"]
    assert [r["meta"] for r in rows] == ["NSF new", "NSF old"]   # newer exp first
    assert rows[0]["active"] is True and rows[1]["active"] is False
```

- [ ] **Step 2: Run to verify it fails.**

Run: `python3 -m pytest facultyfolio/tests/test_funding_view.py -q`
Expected: failures (old `funding_view` still emits amount/unit/copi + dollar summaries).

- [ ] **Step 3: Rewrite `funding_view` in `facultyfolio/render.py`.**

Replace the whole `funding_view` function with:

```python
def funding_view(f: dict, today: datetime.date = None) -> dict | None:
    """Profile 'Research funding' view-model — PI-led awards by NAME, no dollars.
    NSF (lead-PI) then NIH (contact-PI only). None when there are no PI-led awards."""
    fund = f.get("funding") or {}
    today = today or datetime.date.today()
    fy_now = today.year + 1 if today.month >= 10 else today.year
    groups, updated = [], []

    nsf = fund.get("nsf")
    if nsf:
        rows = [a for a in nsf.get("awards", []) if a.get("at_njit")]
        if rows:
            updated.append(nsf.get("updated_at"))
            rows = sorted(rows, key=lambda a: (_exp_date(a["exp"]) or datetime.date.min), reverse=True)
            n = len(rows)
            groups.append({
                "agency": "NSF",
                "summary": f'{n} award{"" if n == 1 else "s"} · as Principal Investigator',
                "rows": [{
                    "title": a["title"], "url": _NSF_LINK.format(a["id"]),
                    "meta": f'NSF {a["id"]}',
                    "years": f'{_year(a["start"])} – {_year(a["exp"])}',
                    "active": bool(_exp_date(a["exp"]) and _exp_date(a["exp"]) >= today),
                } for a in rows],
            })

    nih = fund.get("nih")
    if nih:
        contact = [p for p in nih.get("projects", []) if p.get("role") == "contact"]
        if contact:
            updated.append(nih.get("updated_at"))
            contact = sorted(contact, key=lambda p: (p.get("fy_last") or 0), reverse=True)
            m = len(contact)
            groups.append({
                "agency": "NIH",
                "summary": f'{m} project{"" if m == 1 else "s"} · as Contact PI',
                "rows": [{
                    "title": p["title"],
                    "url": _NIH_LINK.format(p["appl_id"]) if p.get("appl_id") else None,
                    "meta": f'NIH {p["core"]}',
                    "years": f'FY{p["fy_first"]} – FY{p["fy_last"]}' if p.get("fy_first") else "—",
                    "active": bool(isinstance(p.get("fy_last"), int) and p["fy_last"] >= fy_now),
                } for p in contact],
            })

    if not groups:
        return None
    src = " and ".join(g["agency"] for g in groups)
    dates = [u for u in updated if u]
    prov = f"From {src} public award records"
    if dates:
        prov += f" · as of {F.date_long(min(dates))}"
    return {"groups": groups, "provenance": prov}
```

- [ ] **Step 4: Run to verify it passes.**

Run: `python3 -m pytest facultyfolio/tests/test_funding_view.py -q`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit.**

```bash
git add facultyfolio/render.py facultyfolio/tests/test_funding_view.py
git commit -m "feat(facultyfolio): funding_view v2 — award names + counts, no dollars, NIH contact-only"
```

---

### Task 2: Profile template rows + CSS (drop dollar cell)

**Files:**
- Modify: `facultyfolio/templates/profile.html` (funding rows)
- Modify: `facultyfolio/assets/style.css` (`.fund-*`)
- Test: `facultyfolio/tests/test_profile_funding_render.py` (rewrite)

**Interfaces:**
- Consumes: Task 1's `funding_view` shape (no amount/unit/copi).

- [ ] **Step 1: Rewrite the test file.**

Replace `facultyfolio/tests/test_profile_funding_render.py` entirely with:

```python
import re
from facultyfolio import db, render


def _funding_section(html):
    # the #funding <section> ... first </section> after it
    start = html.index('id="funding"')
    return html[start:html.index("</section>", start)]


def test_funded_profile_has_funding_section_no_dollars():
    f = db.get_faculty("zhiwei")
    html = render.render_profile(f)
    assert 'class="eyebrow">Sponsored research' in html
    assert "Research funding" in html
    sec = _funding_section(html)
    assert "as Principal Investigator" in sec or "as Contact PI" in sec
    assert "reporter.nih.gov/project-details/" in sec        # NIH links resolve (appl_id live)
    # no dollars in the scaffolding (strip the verbatim <a/span class="fund-t"> titles first)
    scaffold = re.sub(r'class="fund-t"[^>]*>.*?<', "<", sec, flags=re.S)
    assert "$" not in scaffold
    assert "fund-cite" not in sec                            # dollar cell removed
    assert "co-PI" not in sec                                # co-PI chip gone


def test_unfunded_profile_has_no_funding_section():
    f = dict(db.get_faculty("zhiwei")); f["funding"] = {}
    assert "Research funding" not in render.render_profile(f)


def test_funded_but_no_scholar_still_renders_funding():
    f = db.get_faculty("calvin")
    if not f.get("funding"):
        import pytest; pytest.skip("fixture slug not funded; pick another no-Scholar funded slug")
    f = dict(f); f["scholar"] = None
    assert "Research funding" in render.render_profile(f)
```

- [ ] **Step 2: Run to verify it fails.**

Run: `python3 -m pytest facultyfolio/tests/test_profile_funding_render.py -q`
Expected: FAIL (old template still renders `fund-cite` + dollars).

- [ ] **Step 3: Rewrite the funding rows in `facultyfolio/templates/profile.html`.**

Replace the `{% for g in funding.groups %} … {% endfor %}` block (the two nested loops) with:

```html
        {% for g in funding.groups %}
        <div class="fund-group">
          <div class="fund-sum"><span class="ag">{{ g.agency }}</span><span class="amt">{{ g.summary }}</span></div>
          {% for r in g.rows %}
          <div class="fund-row">
            <div class="fund-main">
              {% if r.url %}<a class="fund-t" href="{{ r.url }}">{{ r.title }}</a>{% else %}<span class="fund-t">{{ r.title }}</span>{% endif %}
              <div class="fund-meta"><span>{{ r.meta }}</span><span class="sep">·</span><span>{{ r.years }}</span>{% if r.active %}<span class="chip active">Active</span>{% endif %}</div>
            </div>
          </div>
          {% endfor %}
        </div>
        {% endfor %}
```

- [ ] **Step 4: Update the CSS in `facultyfolio/assets/style.css`.**

Remove the now-unused `.fund-cite` rules and the `.fund-row.copi` / `.fund-meta .chip.copi` rules, and drop the left-cell gap on `.fund-row`. Concretely:

Delete these lines (the dollar-cell block):
```css
  .fund-cite{width:92px; flex-shrink:0; text-align:right;}
  .fund-cite .n{font-family:var(--f-display); font-size:18px; font-weight:600; color:var(--ink); display:block; line-height:1.05; font-variant-numeric:tabular-nums;}
  .fund-cite .l{font-family:var(--f-mono); font-size:10px; text-transform:uppercase; letter-spacing:.04em; color:var(--faint);}
  .fund-row.copi .fund-cite .n{color:var(--faint);}
```
Delete the co-PI chip rule:
```css
  .fund-meta .chip.copi{background:var(--hair); color:var(--mute);}
```
Change `.fund-row` (it no longer has a left cell, so drop the big gap):
```css
  .fund-row{padding:12px 0; border-bottom:1px solid var(--hair-2);}
```
(Also remove the `@media (max-width:540px){ .fund-cite{...} .fund-row{...} }` rule if it references `.fund-cite`; keep any part that doesn't.)

- [ ] **Step 5: Run to verify it passes.**

Run: `python3 -m pytest facultyfolio/tests/test_profile_funding_render.py -q`
Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add facultyfolio/templates/profile.html facultyfolio/assets/style.css facultyfolio/tests/test_profile_funding_render.py
git commit -m "feat(facultyfolio): profile funding rows show names + counts, no dollar cell"
```

---

### Task 3: Rewrite `rank.funding_rollup` (counts + active + funded)

**Files:**
- Modify: `facultyfolio/rank.py` (`funding_rollup` + a `_award_active` helper; ensure `import datetime`)
- Test: `facultyfolio/tests/test_funding_rollup.py` (rewrite)

**Interfaces:**
- Produces: `funding_rollup(org_ids, today=None) -> {"nsf_awards", "nsf_active", "nih_projects", "nih_active", "funded", "as_of"} | None`.

- [ ] **Step 1: Rewrite the test file.**

Replace `facultyfolio/tests/test_funding_rollup.py` entirely with:

```python
import datetime
from facultyfolio import rank, db

TODAY = datetime.date(2026, 7, 10)


def test_cs_rollup_counts():
    r = rank.funding_rollup([16], today=TODAY)          # CS org node id = 16
    assert r["nsf_awards"] == 59 and r["nsf_active"] == 14
    assert r["nih_projects"] == 5 and r["nih_active"] == 1
    assert r["funded"] == 23


def test_ywcc_rollup_counts():
    ywcc = db.org_node_by_slug("ywcc")
    ids = [d["node_id"] for d in db.dept_orgs_of_college(ywcc)] + [ywcc]
    r = rank.funding_rollup(ids, today=TODAY)
    assert r["nsf_awards"] == 92 and r["nsf_active"] == 25
    assert r["nih_projects"] == 5 and r["nih_active"] == 1
    assert r["funded"] == 36


def test_data_science_has_no_nih():
    r = rank.funding_rollup([db.org_node_by_slug("data-science")], today=TODAY)
    assert r["nsf_awards"] == 17 and r["nih_projects"] == 0
    assert r["funded"] == 7


def test_empty_subtree_returns_none():
    assert rank.funding_rollup([], today=TODAY) is None
```

- [ ] **Step 2: Run to verify it fails.**

Run: `python3 -m pytest facultyfolio/tests/test_funding_rollup.py -q`
Expected: FAIL (old rollup returns `nsf`/`nih` dollar keys, no `today` param).

- [ ] **Step 3: Rewrite `funding_rollup` + add `_award_active` in `facultyfolio/rank.py`.**

First confirm `import datetime` exists at the top of `rank.py`; add it if missing. Then add the helper and replace `funding_rollup`:

```python
def _award_active(mdy, today):
    try:
        return datetime.datetime.strptime(mdy, "%m/%d/%Y").date() >= today
    except (ValueError, TypeError):
        return False


def funding_rollup(org_ids, today=None):
    """Per-agency PI-led award COUNTS across the given org node ids' home faculty.
    Dedup by person node id. Returns {nsf_awards, nsf_active, nih_projects,
    nih_active, funded, as_of} or None when the subtree has no PI-led awards."""
    today = today or datetime.date.today()
    fy_now = today.year + 1 if today.month >= 10 else today.year
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
                awards = [a for a in ((fund.get("nsf") or {}).get("awards") or []) if a.get("at_njit")]
                projs = [p for p in ((fund.get("nih") or {}).get("projects") or []) if p.get("role") == "contact"]
                na_act = sum(1 for a in awards if _award_active(a["exp"], today))
                np_act = sum(1 for p in projs if isinstance(p.get("fy_last"), int) and p["fy_last"] >= fy_now)
                dates = [b["updated_at"] for b in (fund.get("nsf"), fund.get("nih"))
                         if b and b.get("updated_at")]
                seen[r["id"]] = (len(awards), na_act, len(projs), np_act, dates, bool(awards or projs))
    finally:
        conn.close()
    nsf_awards = sum(v[0] for v in seen.values())
    nih_projects = sum(v[2] for v in seen.values())
    if nsf_awards == 0 and nih_projects == 0:
        return None
    all_dates = [d for v in seen.values() if v[5] for d in v[4]]
    return {"nsf_awards": nsf_awards, "nsf_active": sum(v[1] for v in seen.values()),
            "nih_projects": nih_projects, "nih_active": sum(v[3] for v in seen.values()),
            "funded": sum(1 for v in seen.values() if v[5]),
            "as_of": min(all_dates) if all_dates else None}
```

- [ ] **Step 4: Run to verify it passes.**

Run: `python3 -m pytest facultyfolio/tests/test_funding_rollup.py -q`
Expected: PASS (4 passed). If a count is off, STOP and report the actual number — do not edit the test to match.

- [ ] **Step 5: Commit.**

```bash
git add facultyfolio/rank.py facultyfolio/tests/test_funding_rollup.py
git commit -m "feat(facultyfolio): funding_rollup v2 — per-agency award counts + active + funded"
```

---

### Task 4: `_rollup_view` + hub/leaderboard templates (counts)

**Files:**
- Modify: `facultyfolio/render.py` (`_rollup_view`)
- Modify: `facultyfolio/templates/hub.html` + `facultyfolio/templates/leaderboard.html` (`.rollup` line)
- Test: `facultyfolio/tests/test_rollup_render.py` (rewrite)

**Interfaces:**
- Consumes: Task 3's `funding_rollup` dict.
- Produces: `_rollup_view(r) -> {"parts": [(label, agency)], "funded": int, "as_of": str} | None`.

- [ ] **Step 1: Rewrite the test file.**

Replace `facultyfolio/tests/test_rollup_render.py` entirely with:

```python
from facultyfolio import render

ROLL = {"nsf_awards": 59, "nsf_active": 14, "nih_projects": 5, "nih_active": 1,
        "funded": 23, "as_of": "2026-07-10"}
ROLL_NSF_ONLY = {"nsf_awards": 17, "nsf_active": 8, "nih_projects": 0, "nih_active": 0,
                 "funded": 7, "as_of": "2026-07-10"}


def test_rollup_view_both_agencies_counts():
    v = render._rollup_view(ROLL)
    assert v["parts"] == [("59 awards (14 active)", "NSF"), ("5 projects (1 active)", "NIH")]
    assert v["funded"] == 23
    assert v["as_of"] == "Jul 2026"
    assert "$" not in repr(v)


def test_rollup_view_omits_zero_agency():
    v = render._rollup_view(ROLL_NSF_ONLY)
    assert v["parts"] == [("17 awards (8 active)", "NSF")]


def test_rollup_view_singular_and_none():
    one = render._rollup_view({"nsf_awards": 1, "nsf_active": 0, "nih_projects": 0,
                               "nih_active": 0, "funded": 1, "as_of": None})
    assert one["parts"] == [("1 award (0 active)", "NSF")]
    assert one["as_of"] == ""
    assert render._rollup_view(None) is None
    assert render._rollup_view({"nsf_awards": 0, "nih_projects": 0, "funded": 0, "as_of": None}) is None
```

- [ ] **Step 2: Run to verify it fails.**

Run: `python3 -m pytest facultyfolio/tests/test_rollup_render.py -q`
Expected: FAIL (old `_rollup_view` reads `nsf`/`nih` dollar keys).

- [ ] **Step 3: Rewrite `_rollup_view` in `facultyfolio/render.py`.**

```python
def _rollup_view(r: dict | None) -> dict | None:
    """Raw funding_rollup dict -> template-ready {parts:[(label, agency)], funded, as_of}."""
    if not r or (not r.get("nsf_awards") and not r.get("nih_projects")):
        return None
    parts = []
    if r["nsf_awards"]:
        n, a = r["nsf_awards"], r["nsf_active"]
        parts.append((f'{n} award{"" if n == 1 else "s"} ({a} active)', "NSF"))
    if r["nih_projects"]:
        n, a = r["nih_projects"], r["nih_active"]
        parts.append((f'{n} project{"" if n == 1 else "s"} ({a} active)', "NIH"))
    return {"parts": parts, "funded": r["funded"],
            "as_of": F.month_year(r["as_of"]) if r.get("as_of") else ""}
```

- [ ] **Step 4: Run the `_rollup_view` test.**

Run: `python3 -m pytest facultyfolio/tests/test_rollup_render.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Update the `.rollup` line in BOTH templates.**

In `facultyfolio/templates/hub.html` (line ~23) and `facultyfolio/templates/leaderboard.html` (line ~65), replace the `.rollup` `<div>` with:

```html
      <div class="rollup"><span class="rk">Sponsored research</span>{% for label, ag in funding_rollup.parts %}<span><b>{{ ag }}</b> {{ label }}</span><span class="sep">·</span>{% endfor %}<span><b>{{ funding_rollup.funded }}</b> funded faculty</span>{% if funding_rollup.as_of %}<span class="sep">·</span><span class="asof">as of {{ funding_rollup.as_of }}</span>{% endif %}</div>
```

(Changed: `funding_rollup.n` → `funding_rollup.funded`; parts loop is `label, ag` → renders "NSF 59 awards (14 active)"; `as_of` guarded.)

- [ ] **Step 6: Commit.**

```bash
git add facultyfolio/render.py facultyfolio/templates/hub.html facultyfolio/templates/leaderboard.html facultyfolio/tests/test_rollup_render.py
git commit -m "feat(facultyfolio): rollup line shows per-agency award counts, no dollars"
```

---

### Task 5: Remove dead money helpers + update invariants

**Files:**
- Modify: `facultyfolio/format.py` (remove `money`, `money_exact`)
- Modify: `facultyfolio/tests/test_format_money.py` (drop money tests, keep date tests) — or rename
- Modify: `facultyfolio/tests/test_funding_invariants.py` (no `$` in funding scaffolding; NIH no co-PI chip; KEEP the njit_total tripwire)

**Interfaces:** none new — cleanup + guardrail.

- [ ] **Step 1: Confirm `money`/`money_exact` have no remaining callers.**

Run: `grep -rn "money_exact\|\bmoney(" facultyfolio/ --include=*.py | grep -v test_format_money`
Expected: no matches (Tasks 1 & 4 removed the last callers). If any remain, STOP and report.

- [ ] **Step 2: Remove the helpers from `facultyfolio/format.py`.**

Delete the `def money(n)` and `def money_exact(n)` functions. **Keep** `_MONTHS`, `date_long`, `month_year`, `commafy` (still used).

- [ ] **Step 3: Trim `facultyfolio/tests/test_format_money.py`.**

Delete the money tests (`test_money_*`), keep `test_date_long_and_month_year`. The file becomes date-helpers-only (leave it named as-is or the runner is fine).

- [ ] **Step 4: Update `facultyfolio/tests/test_funding_invariants.py`.**

Keep `test_njit_total_equals_sum_of_contributing_rows` and `_funded_slugs` UNCHANGED. Replace the two page-level tests with these (no `$` anywhere in funding scaffolding now):

```python
def test_no_funding_on_hero():
    f = db.get_faculty("zhiwei")
    html = render.render_profile(f)
    hero = html.split('id="funding"')[0]        # everything before the funding section
    assert "$" not in hero
    assert "fund-" not in hero
    assert "rollup" not in hero


def test_profile_funding_section_no_dollars_no_copi():
    f = db.get_faculty("zhiwei")
    html = render.render_profile(f)
    start = html.index('id="funding"')
    sec = html[start:html.index("</section>", start)]
    scaffold = re.sub(r'class="fund-t"[^>]*>.*?<', "<", sec, flags=re.S)   # drop verbatim titles
    assert "$" not in scaffold
    assert "co-PI" not in sec and "fund-cite" not in sec


AGGREGATE_PAGES = ["ywcc/computer-science/index.html", "ywcc/index.html", "index.html"]


def test_aggregate_pages_no_dollars_no_fund_classes():
    for rel in AGGREGATE_PAGES:
        out = os.path.join(config.OUT_ROOT, rel)
        assert os.path.exists(out), f"build the site first: {rel}"
        html = open(out).read()
        assert "fund-" not in html          # profile-only classes never leak here
        assert "$" not in html              # no dollars anywhere on aggregate pages
```

Ensure the imports at the top include `re` (add if missing).

- [ ] **Step 5: Build + run the invariants.**

Run:
```bash
python3 -m facultyfolio.build >/dev/null 2>&1
python3 -m pytest facultyfolio/tests/test_funding_invariants.py facultyfolio/tests/test_format_money.py -q
```
Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add facultyfolio/format.py facultyfolio/tests/test_format_money.py facultyfolio/tests/test_funding_invariants.py
git commit -m "chore(facultyfolio): drop dead money helpers; invariants assert no-\$ funding + no co-PI"
```

---

### Task 6: Full rebuild + spot-check + deploy (owner-gated)

**Files:** none (build + deploy)

- [ ] **Step 1: Full suite + full rebuild.**

Run:
```bash
python3 -m pytest facultyfolio/tests/ -q
python3 -m facultyfolio.build
```
Expected: all green; `built N faculty`.

- [ ] **Step 2: Spot-check the built pages.**

Confirm on `config.OUT_ROOT`:
- `p/zhiwei.html` — funding section shows award **names** (no `$`), "as Principal Investigator" / "as Contact PI", NIH links resolve, Active chips.
- `ywcc/computer-science/index.html` — rollup reads "NSF 59 awards (14 active) · NIH 5 projects (1 active) · 23 funded faculty", no `$`.
- `ywcc/index.html` + root `index.html` — rollups present, no `$`.

Grep aid:
```bash
grep -o 'class="rollup".\{0,140\}' /home/md724/Faculty-Folio/ywcc/computer-science/index.html
grep -c '\$' /home/md724/Faculty-Folio/p/zhiwei.html   # expect 0
```

- [ ] **Step 3: Deploy (owner-gated — confirm before pushing).**

```bash
cd /home/md724/Faculty-Folio && git add -A && git commit -m "Funding: switch to award names + activity counts (no dollars)" && git push origin main
```

- [ ] **Step 4: Merge to main + push source (owner-gated).**

```bash
cd /home/md724/gsa-gateway && git checkout main && git merge --no-ff feat/facultyfolio-funding-v2 -m "Merge: FacultyFolio funding v2 (awards + counts, no dollars)" && git push origin main
```

---

## Self-Review

- **Spec coverage:** no dollars (T1/T2/T4/T5 + invariants); NSF PI-only + NIH contact-only, co-PI dropped (T1 filter + T2 template + T5 invariant); PI wording (T1 summaries); profile rows keep name+#+range+Active (T2); rollup per-agency counts + active + funded, never summed (T3/T4); money helpers removed (T5); njit_total tripwire retained (T5). ✓
- **Placeholder scan:** every step carries real code; count targets are concrete. ✓
- **Type consistency:** `funding_view` rows drop amount/unit/copi (T1) — template reads only title/url/meta/years/active (T2) ✓. `funding_rollup` returns nsf_awards/nsf_active/nih_projects/nih_active/funded/as_of (T3) — `_rollup_view` consumes exactly those (T4) — template reads parts/funded/as_of (T4) ✓. `today` param added to both funding_view (already had it) and funding_rollup (T3), used by tests ✓.
