# Scholar Refresh Job (dashboard, scoped) — design

**Date:** 2026-06-20
**Status:** DESIGN — approved by Mohammad (shape); awaiting spec review + senior-eng review before build (TDD)
**Builds on:** the external-profiles Scholar work (`v2/core/ingestion/scholar.py`, `scripts/refresh_scholar.py`,
[[project_external_profiles]] bullet-3 interests→areas) and the Jobs control plane (`bot/services/jobs.py`,
`v2/local_server.py`, dashboard Jobs tab).

## Problem / goal

Scholar metrics + interests→research-areas are refreshed today only by running `scripts/refresh_scholar.py`
from a shell over **everyone** with a Scholar URL. Mohammad wants to refresh **more frequently** and to
**scope a run to a college or department** from the dashboard, without re-hitting every Scholar page each
time (politeness + Scholar block-risk). So: a **Scholar Refresh job in the dashboard Jobs tab**, with a
**college/department scope dropdown** and a **"only if older than N days" staleness filter**.

Manual button only (no automated scheduling this round — explicitly deferred).

## Decisions (locked with Mohammad, 2026-06-20)

- **Scope dropdown granularity:** All faculty + per-college + per-department (one list, sourced from the
  `organizations` tree). College = whole subtree; department = just that dept.
- **Staleness filter:** "only refresh profiles older than N days," default **30** (editable).
- **Trigger:** manual button now. Automated scheduling = deferred (its own future feature).

## Architecture

Five thin pieces, each independently testable; selection is separated from execution so the network-free
logic can be unit-tested.

### 1. Selection helper (new, pure-ish) — `v2/core/ingestion/scholar.py`
```python
def select_scholar_targets(conn, *, org_scope: str | None = None,
                           older_than_days: int | None = None,
                           today: date | None = None) -> list[str]:
    """Person keys eligible for a Scholar refresh: have a Scholar URL, optionally restricted to
    an org subtree, optionally only those whose scholar.updated_at is older than N days."""
```
- Base set = `people_with_scholar(conn)` (existing).
- `org_scope`: resolve the org + **all descendant orgs** by reusing `skills.org_descendants(conn, org_id)`
  (already "org itself + every active descendant" — the same resolver "people in YWCC" uses), map each to its
  node via `orgs.org_node_id`, then keep only people with an active `has_role` edge into any of those nodes.
  `org_scope` is an org **slug**; resolve slug→org_id via `organizations`.
- `older_than_days`: drop anyone whose `scholar.updated_at` is within N days of `today`. (See staleness
  note below.) `None` ⇒ no age filter; `org_scope=None` ⇒ all faculty.
- Pure read; no commit; no network.

### 2. Execution — generalize `refresh_scholar`
Change `only_key: str | None` → `only_keys: set[str] | None` (back-compat: callers passing one key wrap it;
the CLI `--key` still works). `refresh_scholar` stays focused on fetch→parse→`set_person_profiles` +
`set_person_research_areas`. The dashboard/CLI computes the target set via `select_scholar_targets` and
passes `only_keys`. Returns the same stats dict (`people, updated, areas_updated, failed, errors`).

### 3. CLI — `scripts/refresh_scholar.py` (stays gated, dry-run default)
New args: `--org <slug>` / `--department <slug>` (→ `org_scope`), `--older-than <days>` (→ `older_than_days`),
`--embed` (run `embed_all` after a successful `--commit`, so new research-area KB items are searchable).
Dry-run prints the resolved target keys + count (so a scoped run is previewable before `--commit`).

### 4. Job plumbing — mirrors existing jobs exactly
- `bot/services/jobs.py`: `build_refresh_scholar_command(*, python_bin, repo_root, db_path, org_scope,
  older_than_days, embed)` → `python scripts/refresh_scholar.py --commit [--org X | --department X]
  [--older-than N] [--embed] --db <path>`. Add a `"refresh_scholar"` branch to `_default_build_cmd`.
  `JobManager.start_refresh_scholar(org_scope=None, older_than_days=30, embed=True)`.
- `v2/local_server.py`: `POST /api/jobs/refresh-scholar` (body: `{scope, older_than, embed}`) →
  `JOBS.start_refresh_scholar(...)`; 409 if a job is already running (same guard as the other jobs).

### 5. Dropdown data + dashboard UI
- The jobs/health API returns a **scope list**: each entry `{slug, label, type: college|department, eligible}`
  where `eligible` = # people in that subtree with a Scholar URL. Plus an "All faculty (N with Scholar)" entry.
  Built from the `organizations` tree + a per-subtree Scholar-URL count.
- Dashboard Jobs tab: a **Refresh Google Scholar** job card with a **scope `<select>`** (grouped All /
  Colleges / Departments, each showing its eligible count), an **"older than [30] days"** number input, and a
  **Run** button. Progress/result surfaced like the other jobs (the existing job-status polling).

## Data flow
`button → POST /api/jobs/refresh-scholar {scope, older_than, embed} → JobManager.start_refresh_scholar →
subprocess: refresh_scholar.py --commit --org … --older-than … --embed → select_scholar_targets →
refresh_scholar(only_keys=…) → set_person_profiles + set_person_research_areas (per person, polite delay) →
embed_all → stats in job log → dashboard shows summary.`

## Staleness note (minor, flagged)
`scholar.updated_at` is currently stored as `YYYY-MM` (month granularity), so "older than N days" is only
month-precise today. Fix: write new `updated_at` as full `YYYY-MM-DD` (old `YYYY-MM` values still compare
correctly as month-start, so it's backward-compatible). Then `select_scholar_targets` does an exact date
diff. This is the one storage tweak in scope.

## Error handling / safety
- Gated as today: `--commit` takes a `hardened_backup`; dry-run otherwise. One job at a time (409 guard).
- Per-person fetch failures are counted (`failed`/`errors`) and skipped — one bad profile never aborts the run.
- Politeness: keep the inter-fetch `--delay` (default 2–3s). The staleness filter further limits volume on
  frequent runs. `default_fetch` (urllib) is the provider (the backfill showed it works at this cadence);
  provider stays injectable for a future swap.

## Testing (TDD)
- `select_scholar_targets`: no scope ⇒ all scholar people; college scope ⇒ includes its departments'
  people; department scope ⇒ only that dept; staleness boundary (updated today excluded, updated >N days
  ago included); person with Scholar URL but no org still included when no scope.
- `refresh_scholar` honors `only_keys` (subset fetched; mock fetch).
- `build_refresh_scholar_command`: arg mapping for each scope/older-than/embed combo.
- `start_refresh_scholar` dispatch (injected `build_cmd`, no network/subprocess).
- API route returns 409 when busy; scope-list endpoint returns colleges+departments with eligible counts.
- Grow `eval/questions.txt` only if a new answerable surface appears (this is an ops job, so likely none).

## Out of scope (deferred, explicitly)
- **Automated scheduling** (nightly/weekly) — future feature; manual button only now.
- Acquiring **new** Scholar URLs for people who lack one (a refresh only touches people who already have a
  URL) — separate from this job; relates to the "build links from personal websites" idea.

## Goals checklist (fill at PR time)
- [ ] Scope dropdown: All + college + department, from the org tree, with eligible counts
- [ ] Staleness filter "older than N days" (default 30); updated_at → full date
- [ ] `select_scholar_targets` (org subtree + staleness), separated from execution
- [ ] Job plumbing (build cmd + start + dispatch + API route + 409 guard)
- [ ] `--embed` so new research-area items are searchable
- [ ] Manual button (scheduling DEFERRED — loudly flagged)
- [ ] Acquiring new Scholar URLs — OUT OF SCOPE (flagged)
