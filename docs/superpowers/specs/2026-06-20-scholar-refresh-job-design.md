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
- Base set = `people_with_scholar(conn)` (existing; already filters `p.is_active=1`).
- `org_scope` (an org **slug**; resolve slug→org_id via `organizations`): **copy the proven query from
  `skills.top_people_by_metric` (`skills.py:247-264`)** — `sorted(org_descendants(conn, org_id))` then
  `JOIN nodes o ON json_extract(o.attrs,'$.org_id') IN (?,…)` over the descendant **org_ids directly**.
  Do **NOT** call `orgs.org_node_id` per descendant — it is *not* read-only (it can upsert a node), which
  would break this helper's read-only contract; the `json_extract(... org_id) IN (…)` join needs no node-id
  mapping. Filter `e.is_active=1 AND p.is_active=1 AND o.is_active=1` and **`GROUP BY p.id` / return distinct
  person keys** so a person with roles in two in-scope orgs (e.g. two YWCC depts) is fetched exactly once.
- `older_than_days`: drop anyone whose `scholar.updated_at` is within N days of `today`. `None` ⇒ no age
  filter; `org_scope=None` ⇒ all faculty.
- Pure read; **no commit; no node upsert; no network.** Returns a de-duplicated list of person keys.

### 2. Execution — generalize `refresh_scholar`
Change `only_key: str | None` → `only_keys: set[str] | None` (back-compat: callers passing one key wrap it;
the CLI `--key` still works). `refresh_scholar` stays focused on fetch→parse→`set_person_profiles` +
`set_person_research_areas`. The dashboard/CLI computes the target set via `select_scholar_targets` and
passes `only_keys`. Returns the same stats dict (`people, updated, areas_updated, failed, errors`).

### 3. CLI — `scripts/refresh_scholar.py` (stays gated, dry-run default)
New args: `--org <slug>` / `--department <slug>` (→ `org_scope`), `--older-than <days>` (→ `older_than_days`),
`--embed`. Dry-run prints the resolved target keys + count (via `select_scholar_targets`) before `--commit`.
**`--embed`**: after a successful `--commit`, **shell out** to `v2/scripts/embed_all.py` as a separate
process (not an import — keeps sqlite-vec/Ollama deps out of the gated writer; matches `build_explore_command`
which bundles `--embed`). `embed_all` is **resumable** → embeds ONLY the new `research_areas` items
`set_person_research_areas` inserted, not the whole corpus. **Embed failure must NOT undo the committed
metrics/areas write** (the data is already committed): on embed error, log it and report "refreshed but not
embedded — run embed when Ollama is up" — never fail the whole job's data write. (Embed needs Ollama up.)

### 4. Job plumbing — mirrors existing jobs exactly
- `bot/services/jobs.py`: `build_refresh_scholar_command(*, python_bin, repo_root, db_path, org_scope,
  older_than_days, embed)` → `python scripts/refresh_scholar.py --commit [--org X | --department X]
  [--older-than N] [--embed] --db <path>`. Add a `"refresh_scholar"` branch to `_default_build_cmd`.
  `JobManager.start_refresh_scholar(org_scope=None, older_than_days=30, embed=True)`.
- `v2/local_server.py`: `POST /api/jobs/refresh-scholar` (body: `{scope, older_than, embed}`) →
  `JOBS.start_refresh_scholar(...)`; 409 if a job is already running. **Input validation mirroring
  `_api_refresh`:** reject an unknown `scope` slug → 400, coerce `older_than` to int → 400 on bad input.

### 5. Dropdown data + dashboard UI
- **Scope list = its OWN endpoint** (e.g. `GET /api/jobs/scholar-scopes`), fetched **once when the Jobs tab
  opens — NOT on the hot `_api_health` poll** (which `refreshJobsHealth()` calls on a timer). Computed in
  **ONE pass:** fetch every (person, has-scholar?, in-scope org_ids) once, then roll the Scholar-URL counts up
  the org parent-chain in Python — not N subtree-walks × a LIKE scan per poll. Each entry
  `{slug, label, type: college|department, eligible}` + an "All faculty (N with Scholar)" entry.
- Dashboard Jobs tab: reuse the **existing "Refresh: [what] [target]" pattern** (`app.js:585-644`) rather than
  a new card — add **"Google Scholar metrics"** as a `refresh-what` option whose `refresh-target` is the scope
  list (grouped All / Colleges / Departments, each showing its eligible count), plus an **"older than [30]
  days"** number input. Reuses the existing run/confirm/poll wiring. Result surfaced like the other jobs.

## Data flow
`button → POST /api/jobs/refresh-scholar {scope, older_than, embed} → JobManager.start_refresh_scholar →
subprocess: refresh_scholar.py --commit --org … --older-than … --embed → select_scholar_targets →
refresh_scholar(only_keys=…) → set_person_profiles + set_person_research_areas (per person, polite delay) →
embed_all → stats in job log → dashboard shows summary.`

## Staleness note (RESOLVED — commit to full date)
`scholar.updated_at` is stored today as `YYYY-MM` (month granularity), which makes the "older than N days"
filter month-precise — and that **contradicts** the test "updated today excluded" (it can't pass at month
resolution). **Decision: write new `updated_at` as full `YYYY-MM-DD`** (in `refresh_scholar`, change the
`strftime("%Y-%m")` to `%Y-%m-%d`). `select_scholar_targets` parses whatever's stored — a legacy `YYYY-MM`
value is treated as month-start (fully back-compat), a `YYYY-MM-DD` exactly — so the age diff is exact for
refreshed people and the filter supports any N (better for frequent checks). Display impact: the metric
suffix renders `— as of {updated}` verbatim (`profile_fields.py:135-137`), so refreshed people show a
full date; this is a deliberate, minor, day-precise display improvement (no render-code change).

## Error handling / safety
- Gated as today: `--commit` takes a `hardened_backup`; dry-run otherwise. One job at a time (409 guard).
- Per-person fetch failures are counted (`failed`/`errors`) and skipped — one bad profile never aborts the run.
- Politeness: keep the inter-fetch `--delay` (default 2–3s). The staleness filter further limits volume on
  frequent runs. `default_fetch` (urllib) is the provider (the backfill showed it works at this cadence);
  provider stays injectable for a future swap.
- **Job summary line (N4):** the CLI prints a completion line the job summarizer recognizes — add a
  `refresh_scholar` branch to `jobs.py _summarize` (or emit a line matching its grep), so the dashboard shows
  "N updated, M areas, K failed" instead of falling back to the last (error) line.

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

## Senior-eng review outcome (2026-06-20) — folded in
Verdict **ship-with-fixes** (no blockers). Folded: S1 (pin selection to the `top_people_by_metric` query —
`org_descendants` + `json_extract org_id IN`, drop `org_node_id`, distinct keys, read-only), S2 (commit to
`YYYY-MM-DD`, parse legacy `YYYY-MM` as month-start — resolves the test/format contradiction), S3 (`--embed`
shells out, post-commit, embed failure never undoes the write), S4 (scope counts on their own endpoint /
one query, off the health poll), N1 (reuse the "Refresh: [what][target]" UI), N2 (route input validation),
N3 (`only_key`→`only_keys` filter detail), N4 (recognizable summary line). No goal silently dropped.

## Goals checklist (fill at PR time)
- [ ] Scope dropdown: All + college + department, from the org tree, with eligible counts
- [ ] Staleness filter "older than N days" (default 30); updated_at → full date
- [ ] `select_scholar_targets` (org subtree + staleness), separated from execution
- [ ] Job plumbing (build cmd + start + dispatch + API route + 409 guard)
- [ ] `--embed` so new research-area items are searchable
- [ ] Manual button (scheduling DEFERRED — loudly flagged)
- [ ] Acquiring new Scholar URLs — OUT OF SCOPE (flagged)
