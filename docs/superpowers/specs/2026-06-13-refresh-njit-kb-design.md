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

Add to `v2/core/ingestion/departments.py`:

```python
def supported() -> list[Department]:
    """Departments whose faculty list is statically discoverable (crawlable today)."""
    return [d for d in DEPARTMENTS.values() if d.discovery == "static"]
```

Today that is **CS** and **Informatics**. **DS** is `discovery="js"` (JavaScript-
rendered list — static discovery finds nothing), so it is excluded until a headless
fetch is built. Adding/curating a department = one registry entry; the button,
"all" run, and UI all follow automatically.

**Status to watch (per the user):**
- **Informatics** is marked `static` but has **not been verified** — v1 only
  exercised CS. It uses the same people.njit.edu profile template, so it *should*
  crawl, but the first all-run is the proof. If it yields 0 profiles or bad data,
  flip it to `js` (greyed) until fixed.
- **DS** stays greyed (`js`) until JS discovery exists.

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

Behavior:
- `--all` is mutually exclusive with `--department`/`--url`.
- Each department prints a clear header into the log; a failure on one department is
  logged and the run **continues** to the next (one bad department must not abort the
  others). The script **exits non-zero if any department failed**, else zero — that
  exit code is what drives the job's `failed`/`done` status in `JobManager` (no
  separate signalling).
- The per-entity diff still lands in `logs/ingest_changes.log`; the run ends with a
  combined summary line per department (e.g. `CS: +3 ~5 -0`, `Informatics: …`).

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
  - `departments: {supported: ["cs","informatics"], unsupported: ["ds"]}`
    (keys + display names), and
  - `last_refresh_all: {duration_seconds, web, finished_at} | null` for the estimate.

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
