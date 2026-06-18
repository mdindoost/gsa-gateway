# MTSM Multi-Org Crawl — Design

**Date:** 2026-06-18
**Status:** IMPLEMENTED + dev-copy validated (senior-reviewed GO-WITH-CHANGES; all must-fixes applied)

## Resolution (post-review + dev-copy run)
- **M1** — kept the `business-data-science` org as `type='program'` (not department); fixed
  `org_departments` to filter `type='department'` so the PhD program isn't listed as a department.
- **C3 reappointment** — REVERSED for MTSM. MTSM has no departments, so faculty live on the
  `mtsm` college itself; reappointing a "Leadership" person to `mtsm` would collide with their
  `faculty@mtsm` edge (one has_role per person+org). Kept the dean→parent cue as `"dean"` only
  (YWCC's 'Dean'/'Associate Deans' sections); MTSM "Leadership" stays `admin@mtsm-administration`
  + `faculty@mtsm` (two clean edges). Added `leadership→admin` as a category rule only.
- **Bugs caught on the dev copy (not in review):** (1) `mtsm-administration` was orphaned
  (parent_id=NULL) because `/administration` crawled before the `mtsm` org existed; (2) the
  crawler's `ensure_org` defaulted `mtsm` to `type='unit'` not `college`. Fixed by adding
  `EntryPoint.org_type` (passed into `ensure_org`) and **ordering** `ALL_ENTRY_POINTS =
  [ROOT, MTSM_FACULTY, MTSM_ADMIN]` so the parent college is created before its sub-unit.
- **M2/M3** — `mtsm_ingest.py` FAQ keys now stable (enumerate index, not `hash()`); the
  PEOPLE list removed (crawler owns all MTSM people).
- **C1** — `verify_kg` now asserts MTSM has zero `type='department'` children.
- **H1/C2** — `run_explore.py` loops all entry points, runs `reconcile_departures` once after
  the loop, and warns on `--reset` if any entry point errored.
- **Dev-copy verification:** +49 MTSM people, org tree correct, Dean = 1 node / 2 edges,
  faculty research enriched, BDS 23 KB items, prose idempotent (FAQ stays 17 on re-run),
  `verify_kg` ✓.

---

**Original status:** PROPOSED (Spec-B slice: extend the YWCC crawler to a second college)
**Goal:** Ingest the entire Martin Tuchman School of Management (MTSM) — all admin + faculty —
into the KG + KB, fully profile-enriched, **crawler-managed** (future re-crawls maintain it),
reusing the existing `explore()` engine with the smallest possible change.

## Finding (verified against live pages)

MTSM renders people on the **same NJIT template** the YWCC crawler already parses. Ran the
real `discovery.parse_listing` over the live HTML (via `explore.http_fetch`, project UA):

| Page | parse_listing result |
|---|---|
| `management.njit.edu/administration` | **13 people** — sections: Leadership, Program Directors, Academic Advisors, Administrative Staff, Corporate Relations |
| `management.njit.edu/faculty` | **46 people** — sections: Distinguished Professors, Professors, Associate Professors, … |
| `management.njit.edu/people` | redirects → `/faculty` |

Profiles are `people.njit.edu/profile/<slug>` — the shared NJIT profile system the existing
`njit_adapter`/`parse_entity` already extracts (research areas, education, courses, email).
**No new parser is needed.** ~50 unique people (some overlap, e.g. Gopalakrishnan in both).

## Org structure (mirrors YWCC)

```
njit
└── mtsm                 Martin Tuchman School of Management (MTSM)   [college]
    └── mtsm-administration   MTSM Administration                     [unit]
```

- `/faculty`        → listing for **mtsm** (parent njit)
- `/administration` → listing for **mtsm-administration** (parent mtsm)

This mirrors YWCC (which has `college-administration` under `ywcc`). The two-org split is the
key decision: it prevents **appointment collisions**. `project_appointment` upserts ONE
`has_role` edge per (person, org). If both listings mapped to a single `mtsm` org, an overlap
person (faculty who is also a program director) would have their category overwritten by
whichever page is crawled last. With two orgs, they correctly accumulate **two** edges
(faculty @ mtsm + admin role @ mtsm-administration) — exactly the YWCC multi-membership model.

## Changes required

### 1. `entry_points.py` — register MTSM as crawler-managed
Add MTSM listing entry points so re-crawls find them. The current `explore()` takes a single
`start`; add a registry of all roots and have `run_explore.py` iterate.

```python
# entry_points.py
MTSM_FACULTY = EntryPoint("https://management.njit.edu/faculty", "mtsm",
                          "Martin Tuchman School of Management (MTSM)", "listing", "njit")
MTSM_ADMIN   = EntryPoint("https://management.njit.edu/administration", "mtsm-administration",
                          "MTSM Administration", "listing", "mtsm")

ALL_ENTRY_POINTS = [ROOT, MTSM_ADMIN, MTSM_FACULTY]   # admin before faculty (see below)
```

`run_explore.py` loops `for ep in ALL_ENTRY_POINTS: explore(conn, fetch, start=ep, depth=depth)`.
Backward compatible: ROOT stays first; existing YWCC behavior unchanged.

**Order:** crawl `/administration` before `/faculty` so that, for the *org-level* enrich pass,
nothing depends on order (the two orgs are independent edges). Order is not correctness-
critical here because of the two-org split, but we keep admin-first for determinism.

### 2. `discovery.py` — section→category rules for MTSM's section labels
MTSM uses section headers YWCC doesn't. Add rules (precision-first, never guess):

| Section label | Current mapping | Needed |
|---|---|---|
| "Leadership" | None ❌ | **admin** |
| "Program Directors" | staff (via "director") | admin *(decision below)* |
| "Corporate Relations" | None | staff |
| "Distinguished Professors" / "Professors" / "Associate Professors" | faculty ✓ | faculty |
| "Academic Advisors" | advisor ✓ | advisor |
| "Administrative Staff" | staff ✓ | staff |

Add to `_SECTION_RULES` (ordered, specific-first):
```python
(re.compile(r"leadership", re.I), "admin"),
(re.compile(r"program director", re.I), "admin"),   # before the generic 'director'→staff
(re.compile(r"corp(orate)? relations", re.I), "staff"),
```
These are additive and ordered before the generic `director→staff` rule, so YWCC is unaffected.

### 3. Dean-to-college reappointment — extend the cue
`explore()` reappoints a college's dean to the **parent** org (so the Dean leads the COLLEGE,
not the admin sub-unit). Today the cue is `"dean" in section`. MTSM's deans sit under
**"Leadership"**. Extend:
```python
if node.parent_slug and re.search(r"dean|leadership", p.section, re.I):
    # appoint to parent (mtsm) instead of mtsm-administration
```
Result: Oya Tukel (Dean) + the Associate Deans (all in "Leadership" on /administration) get an
`admin` appointment to **mtsm**, not mtsm-administration. Non-leadership admin rows (advisors,
staff, program directors) stay on mtsm-administration.

### 4. KB prose (programs + PhD + FAQ)
The 26 KB items from `scripts/mtsm_ingest.py` (programs, PhD admission, FAQ) are **manual**
(`source='dashboard'`) — they come from program pages, not the people template. Keep them in
that gated script, filed under `mtsm`. Drop the separate `business-data-science` org (MTSM's
listings have no departments; the PhD is a program described in KB, not a roster unit). People
come from the crawler; prose comes from the dashboard script. Clean separation of sources.

## Source tagging & maintenance
- People + their profile enrichment → `source='crawler'` (managed by re-crawl + M3 reconcile).
- Program/PhD/FAQ prose → `source='dashboard'` (manual, protected from `--reset`).
- A future `run_explore.py` (all entry points) re-crawls MTSM alongside YWCC; M3 handles
  departures/moves per-listing, scoped to each org. **No manual ops** for people upkeep.

## Gated run plan (per the v2 gated workflow)
1. `hardened_backup` (online-backup API + integrity check).
2. **Dev copy first:** `cp gsa_gateway.db /tmp/dev.db` → `run_explore.py --db /tmp/dev.db`
   (all entry points) → inspect counts + `verify_kg`.
3. Run `scripts/mtsm_ingest.py --commit` (KB prose) on the dev copy too; verify.
4. Only then run live: explore (live) → `mtsm_ingest.py --commit` (live) → `embed_all.py`.
5. Verify live: "who is the dean of MTSM", "MTSM faculty", "<prof> research area",
   "Business Data Science PhD deadline", "what is the MSM".

## Risks / decisions for review
- **R1 — "Program Directors" category.** They are professors with an admin function. Mapping
  the section to `admin` (on mtsm-administration) + faculty (on mtsm via /faculty) seems right.
  Alternative: leave as `staff`. *Recommend admin.*
- **R2 — Two-org vs one-org.** Two-org (mtsm + mtsm-administration) chosen to avoid edge
  collisions and match YWCC. Confirmed correct given `project_appointment` is per-(person,org).
- **R3 — Overlap people** (e.g. Gopalakrishnan: Leadership + Professors). Two-org split gives
  them two correct edges; no clobber. ✓
- **R4 — `explore()` multi-entry-point loop.** Each `explore()` call runs its own BFS +
  per-listing M3. Running three roots sequentially is safe (independent orgs). Verify the
  final `sync_org_nodes` + `reconcile_departures` still run once after all roots.
- **R5 — depth/frontier.** depth=2 walks listing→profile; personal-site frontier deferred,
  same as YWCC. No change.
```
