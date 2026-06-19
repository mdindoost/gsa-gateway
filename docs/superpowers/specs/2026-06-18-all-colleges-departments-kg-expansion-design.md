# All-Colleges / All-Departments KG Expansion — Design

**Status:** senior-reviewed → blockers resolved → implementing (gated)
**Date:** 2026-06-18
**Author:** session (with maintainer Mohammad)

---
## Post-review resolution (FINAL design — supersedes §3/§5 below where they differ)

Senior review (2026-06-18) raised 3 blockers; a section-header audit of all listings (real
`parse_listing` output) resolved them. Decisions (maintainer-approved):

- **B1/S1 — root orgs are seeded explicitly.** `run_explore.py` calls `ensure_org` for `njit`
  (university) and each college (`nce`,`csla`,`hcad`) BEFORE the crawl loop, so colleges/departments
  never orphan regardless of DB state or EntryPoint order.
- **B3 — section-policy by listing level (not a deny-list).** The audit showed every page is cleanly
  sectioned, and the college page is a faculty ROLL-UP that also carries the dean's office. So:
  - **College listing** → appoint ONLY admin/dean's-office sections to the college org; **SKIP the
    faculty sections** (departments own those people). Kills the roll-up duplication.
  - **Department listing** → appoint member sections (Professors/Associate/Assistant/Distinguished/
    Lecturers/Staff/Chair/Adjunct/Emeritus) to the dept org. **Joint Appointments → KEEP** (real
    secondary role; person gets both dept edges). No "teaching X courses"/affiliate sections exist on
    these dept pages, but the skip-guard stays for safety.
- **B2 — viable.** Sections are real and labeled; routing is driven by a `section_policy` layer.
- **HCAD split — APPROVED.** HCAD has no department subdomains, but its one `/our-people` page is
  sectioned by school: `Architecture Faculty` / `University Lecturers, Architecture` → `njsoa`
  (New Jersey School of Architecture); `Art + Design Faculty` / `University Lecturers, Art + Design`
  / `Professors of Practice` → `art-design`; `Leadership` / `Staff` → `hcad` (college). This needs
  **section→org routing within one listing** (see mechanism below).
- **N1 — college-dean reappointment fix.** The existing `"dean" in section → reappoint to parent`
  heuristic (`explore.py:147`) would file a college's dean's office under `njit`. For COLLEGE-level
  listings we keep admin sections on the college org itself (suppress the parent reappointment).
- **S2 — `org_type` set per EntryPoint** (`college` / `department` / `school`); `crawl_scope()`
  rewritten to iterate `ALL_ENTRY_POINTS` grouped by parent.
- **S4 — polite delay** added to the crawl loop before scaling to ~1,400 requests.
- **Open/deferred:** Theatre (0 profiles, different template) and the `njit`-root President/cabinet
  source (page lacks the profile template) — both tracked, neither blocks the college/dept rollout.

### Dev-validation finding (fixed): re-file college-filed KB to the home department
The first dev crawl validated the appointment structure (NCE college = admin/staff only, depts
correct, HCAD split correct, 1 legit dual appointment, no orphans) but `verify_kg` flagged ~40
people with "no KB content." Cause: a **department chair** reached via the college roll-up page
(kept as `nce` admin) has their profile processed *during the NCE crawl, before their department
appointment exists*, so `_home_dept_org_id` is None and KB lands under the college; their dept-page
profile is then skipped as unchanged. `reconcile_departures` retired that "mis-filed" KB, leaving
zero. Fix: `reconcile_departures` now **re-files** KB filed under a non-home org to the home
department (matching by `natural_key`, retiring only true duplicates) instead of blindly retiring —
so a chair's bio/publications end up under their department. Preserves the genuine-dept-move
behavior. (explore.py `reconcile_departures`; tested in test_m3_departures.py.)

### Mechanism: `section_policy` (new, small)
A pure function the `kind=="listing"` branch consults per person:
`route(listing_slug, section) -> target_org_slug | None` (None = skip, no edge). Backed by a
per-listing config: default = the listing's own org; college listings = {admin-section→college,
faculty-section→None}; HCAD listing = {arch-sections→njsoa, artdesign-sections→art-design,
leadership/staff→hcad, centers/IT→None}. Department listings = default (everything→dept) minus an
affiliate/teaching skip set. Unit-tested in isolation; keeps `explore.py` changes minimal.
---

## Goal

Extend the crawler's people/role coverage from **2 colleges** (YWCC + MTSM) to **all five
people-bearing NJIT colleges**, filed in a faithful `njit → college → department` org tree, so
every grad student's professors and departments are represented (engagement). Add **NCE, CSLA,
HCAD**. **Honors is intentionally excluded** (yields no net-new people — see §4).

No new parser is required: every NJIT person renders on the shared `people.njit.edu/profile/<slug>`
template that `parse_entity` already handles, and every college/department people page is a
`people.njit.edu/profile/` link list that `parse_listing` already handles. **The work is adding
`EntryPoint`s + two data-quality guards + a gated rollout.**

## 1. Org tree (and therefore EntryPoint order)

```
njit  (university root)                         ← President / Provost / cabinet  (§6 open item)
├── YWCC   (college, in)   → CS · Data Science · Informatics · College Administration
├── MTSM   (college, in)   → mtsm-administration
├── NCE    (college, NEW)  → 6 departments (below)
├── CSLA   (college, NEW)  → 7 departments (below)
└── HCAD   (college, NEW)  → college-level only (schools not split on the source; §7)
```

Anchor/crawl order is **top-down**: the college listing (creates the college org) precedes its
department listings (which resolve `parent_slug=<college>`). This satisfies the existing invariant
("a sub-unit listing must follow the listing that creates its parent org").

## 2. EntryPoint table (URLs discovered + verified 2026-06-18)

All `kind="listing"`. Counts = distinct `profile/<slug>` links on the page (≈, with cross-listing
overlap). College listings create the college org (`type="college"`, `parent_slug="njit"`);
department listings create the department org (`type="department"`, `parent_slug=<college>`).

### NCE — `nce`, "Newark College of Engineering", parent `njit`
| Order | Org slug | Name | URL | ~People |
|---|---|---|---|---|
| 1 | `nce` | Newark College of Engineering | https://engineering.njit.edu/our-people | 166 |
| 2 | `biomedical-engineering` | Biomedical Engineering | https://biomedical.njit.edu/people | 57 |
| 3 | `chemical-materials-engineering` | Chemical & Materials Engineering | https://cme.njit.edu/people | 46 |
| 4 | `civil-environmental-engineering` | Civil & Environmental Engineering | https://civil.njit.edu/people | 78 |
| 5 | `electrical-computer-engineering` | Electrical & Computer Engineering | https://ece.njit.edu/our-people | 60 |
| 6 | `mechanical-industrial-engineering` | Mechanical & Industrial Engineering | https://mie.njit.edu/faculty | 68 |
| 7 | `applied-engineering-technology` | School of Applied Engineering & Technology | https://appliedengineering.njit.edu/our-people | 34 |

### CSLA — `csla`, "College of Science and Liberal Arts", parent `njit`
| Order | Org slug | Name | URL | ~People |
|---|---|---|---|---|
| 1 | `csla` | College of Science and Liberal Arts | (no flat college /our-people; create via first dept's parent or a dedicated listing) | — |
| 2 | `biological-sciences` | Biological Sciences | https://biology.njit.edu/our-people | 23 |
| 3 | `chemistry-environmental-science` | Chemistry & Environmental Science | https://chemistry.njit.edu/people | 65 |
| 4 | `history` | History | https://history.njit.edu/people | 20 |
| 5 | `humanities-social-sciences` | Humanities & Social Sciences | https://hss.njit.edu/people | 106 |
| 6 | `mathematical-sciences` | Mathematical Sciences | https://math.njit.edu/our-people | 108 |
| 7 | `physics` | Physics | https://physics.njit.edu/people | 56 |
| 8 | `theater-arts-technology` | Theater Arts & Technology | https://theatre.njit.edu/our-people | 0 ⚠ §7 |

> CSLA has no flat college `/our-people` people list (the college site is a department index).
> Create the `csla` college org first via `ensure_org` (no people) so departments can parent to it.
> The first CSLA EntryPoint should be a college org-creation step or the dept listings carry
> `parent_slug="csla"` and `ensure_org` creates `csla` lazily — confirm in review which is cleaner.

### HCAD — `hcad`, "Hillier College of Architecture and Design", parent `njit`
| Order | Org slug | Name | URL | ~People |
|---|---|---|---|---|
| 1 | `hcad` | Hillier College of Architecture & Design | https://design.njit.edu/our-people | 80 |

> The two schools (Architecture / Art+Design) share the college `/our-people` page; the source does
> not split faculty by school, so HCAD stays college-level for now (§7).

## 3. Guard A — home-appointment only (EntryPoint selection)

`parse_listing` + `project_appointment` add **one `has_role` edge per (person, listing-org)**. A
person on two listings gets two edges (intentional for genuine dual roles, e.g. MTSM faculty who is
also a director). To avoid **false** appointments we only ever **anchor home-appointment listings**:
department faculty/staff pages and a college dean's-office/administration page. We do **NOT** anchor:
- "Faculty Teaching X Courses" sections/pages
- "Affiliated / Courtesy / Adjunct-elsewhere Faculty"
- "Advisory Board", "Dean's Alumni Council", "Joint/Secondary appointments"
- the **Honors College** entirely (§4)

Identity is the profile slug, so a person reached again through any non-anchored page is recognized
as already-existing and gets no second edge (existing behavior — verified in `explore.py:127-159`).

## 4. Honors College — excluded

Honors yields **no net-new people**: everyone listed there is already a professor in their home
college; "Faculty Teaching Honors Courses" is a cross-listing, not an appointment. Per maintainer
(2026-06-18), **do not anchor Honors as a people source.** (If desired later, "teaches honors
courses" may be recorded as a profile *attribute* — never a `has_role`.)

## 5. Guard B — teaching/affiliate section is NOT a role (code-level)

Even within an anchored home listing, a page may include an affiliate/teaching section. `parse_listing`
already returns each person's `section`. Add a deny-list check **before** `project_appointment` in the
`kind=="listing"` branch: if `section` matches an affiliate/teaching/courtesy/advisory pattern, **skip
the appointment** (do not create a `has_role` to this org). Proposed pattern (case-insensitive):

```
teaching\s+\w+\s+courses | affiliat | courtesy | adjunct\s+(?:elsewhere|from) |
advisory\s+board | alumni\s+council | joint\s+appointment | secondary\s+appointment | emeriti?
```

(Review: confirm "emeritus" handling — keep emeriti as faculty, or exclude? Maintainer to decide.)
This is additive and section-scoped; it must not affect YWCC/MTSM sections (Dean, Professors,
Leadership, Staff, Academic Advisors), which carry none of these tokens.

## 6. Open item — `njit`-root President / cabinet

University-wide leaders (President, Provost, university VPs) belong under `njit`, never a college.
But `njit.edu/about/administration` returns **0** `profile/` links (rendered without the shared
template). So the root-admin source needs a **separate approach** (custom parse of that page, or a
small manual `people_editor` seed under an `njit-administration` unit). **Flagged, does not block the
college/department work.** Until done, the President simply isn't in the KG (no wrong data) — the
home-appointment guard ensures he never lands under a college by accident.

## 7. Known source limitations (track, don't block)

- **Theatre** (`theatre.njit.edu/our-people`): 0 profile links — different template; needs a
  Theatre-specific selector or manual entry. Ship CSLA without it; backfill later.
- **HCAD schools** not split (shared `/our-people`); college-level only for now.
- Per-page **section detection** for Guard B depends on `parse_listing` exposing real section
  headers on these new sites; verify on a dev crawl that NCE/CSLA pages carry usable sections
  (if a dept page is flat with no affiliate section, Guard B is a no-op there — acceptable).

## 8. Gated rollout (per-college, repeatable)

Standard gated workflow, **one college at a time** (NCE → CSLA → HCAD):
1. `cp gsa_gateway.db /tmp/dev.db`
2. Add the college's EntryPoints to `ALL_ENTRY_POINTS` (ordered per §1–§2).
3. `python scripts/run_explore.py --db /tmp/dev.db` (walks all roots; M3 reconcile once at end).
4. Inspect: per-dept people counts, **spot-check that no cross-listed person got a wrong-college
   edge** (e.g. an Informatics prof must not have `has_role(nce)`), `python scripts/verify_kg.py`.
5. Promote: run live, then `python v2/scripts/embed_all.py`.
6. Update `crawl_scope()` so the dashboard Jobs tab shows the new colleges/departments.

Re-crawl stays first-class: `run_explore.py` re-walks every root; M3 retires departures/moves.

## 9. Tests

- `verify_kg` invariants extended: every new department has `parent_id=<college>`, every new college
  has `parent_id=njit`; no orphan (`parent_id=NULL`) orgs introduced.
- A unit test for Guard B: a listing row with `section="Faculty Teaching Honors Courses"` (or
  "Affiliated Faculty") produces **no** `has_role` edge, while `section="Professors"` does.
- A fixture-HTML `explore()` test: two listings sharing one slug under different orgs → one Person
  node, edges exactly as the home-appointment rules dictate.
- Optional eval additions: "who is in the physics department", "civil engineering faculty",
  "who teaches mechanical engineering" → department structured answers.

## 10. Risks

- **Cross-listing leakage** inside a dept page if sections are absent → mitigated by Guard B +
  dev-crawl spot-check (§8.4); residual risk is a person filed under a nearby dept, low harm.
- **Slug collisions / name variants** across colleges → identity is slug, not name, so safe.
- **Volume**: ~700+ new people + profile fetches per full crawl → run off-peak; existing polite
  delay + frontier mechanics already bound it.
- **CSLA college org with no people** → ensure it's created cleanly (no empty-listing M3 churn).
```
