# Refresh NJIT KB — Design Spec

**Goal:** One dashboard button — **"Refresh NJIT KB"** — that re-crawls **all
supported NJIT faculty departments** in a single run (CS + Informatics today; more
as they're wired), and shows the admin an **expected duration** based on the last
run. On-demand, no terminal, no scheduler.

**Builds on** the dashboard control plane (`docs/superpowers/specs/2026-06-12-dashboard-control-plane.md`):
same `JobManager`, same `local_server.py` `/api/*`, same Jobs tab. This spec adds
an all-departments job and a time estimate.

**Explicitly dropped:** the monthly scheduler. The same all-departments job can be
put on a timer later with no rework, but it is out of scope here.

---

## 1. Supported departments — one source of truth

Add a `verified: bool` field to `Department` and a helper to
`v2/core/ingestion/departments.py`:

```python
def supported() -> list[Department]:
    """Departments the button refreshes — statically discoverable AND verified."""
    return [d for d in DEPARTMENTS.values() if d.discovery == "static" and d.verified]
```

The button refreshes only **static + verified** departments, so an aspirational
registry entry can never write unverified data into the live KB. Adding/curating a
department = one registry entry; the button, "all" run, and UI follow automatically.

**Status (verified against the repo):**
- **CS** — `static`, **verified** (real crawls run; the only department with KB
  data). → the button runs **CS only today.**
- **Informatics** — `static` but **`verified=False`**: it is an aspirational
  registry entry from commit `90c0042`; the org node exists but it has **never been
  crawled (0 KB items)**. It uses the same people.njit.edu template so it *should*
  work, but it stays out of the button (shown "coming soon") until a confirmed test
  run flips `verified=True`. This avoids writing unverified data into the live KB.
- **DS** — `js` (and unverified): greyed until a headless fetch exists.

Enabling a department later = set `verified=True` after a successful manual test run
(`ingest_faculty.py --department <key> --overview` dry-run, then `--commit`).

## 2. The all-departments run

Add an **`--all` mode to `scripts/ingest_faculty.py`**: when set, it iterates
`departments.supported()` and runs the existing per-department pipeline for each,
in **sequence**, into the same process.

Why script-side (not a loop in the runner):
- **One subprocess** → the `JobManager` stays single-process (spawn + one log + one
  watcher + clean cancel).
- **One auto-backup** at the start of the `--commit` run covers the whole batch
  (vs one backup per department).
- **One combined change-log** and one job log, read end-to-end.

Behavior (blocker resolutions from senior review, folded in):
- **Arg parser:** `--all` joins the existing required mutually-exclusive group
  (`--url` / `--limit` / `--all` — exactly one). `--department` default becomes
  `None`; `--all --department X` is rejected. Because `--limit` is in the same
  group, **`--all` always crawls the full discovered list per department** (no 80
  cap) — that resolves the "what does --limit mean for all" ambiguity.
- **One backup, per-department commit:** `_auto_backup` is split out of `commit()`
  (add a `backup=True` param). `--all` takes **one** verified backup up front, then
  runs each department's discover → parse → `commit(..., default_org_id=dept.default_org_id, backup=False)`.
  This gives a single backup **and** each department its own correct org fallback
  (CS=5, Informatics=7) — no silent mis-filing.
- **0 profiles = failure:** in `--all`, a supported department whose discovery
  returns 0 profiles is recorded as **failed** (it is the unverified-Informatics /
  JS signal), the run continues to the next department, and the process **exits
  non-zero** so the job becomes `failed`. The exit code is the only signal
  `JobManager` needs.
- Each department prints a clear header into the log; per-entity diffs still land in
  `logs/ingest_changes.log`; the run ends with a **combined final line** (e.g.
  `Refresh complete — CS: +3 ~5 -0; Informatics: +1 ~0 -0`) phrased so
  `JobManager._summarize` picks it up.

## 3. JobManager (bot/services/jobs.py)

- `start_refresh_all(web=False)` → inserts a job `type="refresh_all"`,
  `args={"scope":"all","web":web}`, spawns
  `\.venv/bin/python scripts/ingest_faculty.py --all --overview --commit [--web]`.
  Reuses the existing lock (one job at a time), detached spawn, watcher, and
  process-group cancel unchanged.
- Add `duration_seconds` (finished_at − started_at) to `get_job`/`list_jobs` output.
- `estimate_refresh_all(web)` → the `duration_seconds` of the most recent
  **completed** (`done`) `refresh_all` job, preferring one whose `args.web` matches;
  falling back to the latest `done` `refresh_all` regardless of `web`; `None` if
  there is no prior run.

## 4. API (local_server.py)

- `POST /api/jobs/refresh` accepts **either** `{department, limit?, web?}` (single,
  retained for the future) **or** `{scope:"all", web?}` → routes to
  `start_refresh_all`. 409 if a job is already running (unchanged). Same CSRF/Host
  guards.
- `GET /api/health` gains:
  - `departments: {supported: [...], unsupported: [...]}` (keys + display names),
    **derived from the registry** (`departments.supported()` / `DEPARTMENTS`), not a
    hardcoded set — and the `_api_refresh` validation set is derived the same way, so
    there is exactly one source of truth (the existing literal `DEPARTMENTS` set in
    `local_server.py` is removed).
  - `last_refresh_all: {duration_seconds, web, finished_at} | null` for the estimate,
    guarded so an empty jobs table / no prior `done` run returns `null` (never raises).

## 5. Dashboard Jobs tab (replaces the per-department buttons)

- A single **"↻ Refresh NJIT KB"** button → `POST /api/jobs/refresh {scope:"all", web}`.
- Sub-line from `/api/health`: **"Will refresh: Computer Science, Informatics"**, with
  **"DS — coming soon"** shown greyed.
- **Expected time:** from `last_refresh_all` — *"Expected ~18 min (based on the last
  run)"*; if `null`, *"First run — no estimate yet."* The estimate is shown as an
  approximate value and is matched to the current `web` toggle when data exists.
- Unchanged: Ollama-up health gate, the `web` toggle, live log tail (3 s poll),
  Cancel, and the recent-jobs list.

## 6. Testing

- `departments.supported()` returns exactly the `static` departments.
- `ingest_faculty.py --all` expands to the supported keys and is mutually exclusive
  with `--department` (test the selection/arg-validation; crawling itself is not unit
  tested — no network).
- `JobManager.start_refresh_all` builds the right command, runs to `done` with an
  injected fake command, and records `type="refresh_all"`.
- `duration_seconds` is computed for finished jobs.
- `estimate_refresh_all` returns the last completed run's duration (and respects the
  `web` match / fallback / `None` cases).
- API: `POST {scope:"all"}` → 201; `/api/health` includes `departments` and
  `last_refresh_all`.
- `app.js` passes `node --check`.

## 7. Scope / non-goals

**In:** the single all-departments button, the supported-department registry helper,
`--all` ingest mode, the duration estimate, the UI changes.
**Out:** the monthly scheduler; per-department checkboxes in the UI (the button is
all-supported); DS JS-discovery; admin-defined arbitrary crawl sources (each source
needs site-specific crawler work, not a dashboard entry).
