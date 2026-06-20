# Scholar URL Discovery Job — design

**Date:** 2026-06-20
**Status:** DESIGN — shape approved by Mohammad; awaiting spec review + senior-eng AND RAG/anti-fabrication review before build (TDD)
**Builds on:** the Scholar refresh job ([[project_external_profiles]], `2026-06-20-scholar-refresh-job-design.md`),
`v2/core/ingestion/scholar.py`, `v2/integration/njit_search.py` (Brave), the Jobs control plane.

## Problem / goal

Most NJIT faculty have **no Google Scholar URL** in the KG (e.g. ECE 59/60, Applied Eng 33/34, Math 107/108
lack one; ~75 of ~1,076 people total have one). Without a Scholar URL the refresh job can't give them metrics
or research areas — and NJIT profile pages rarely list Scholar. Goal: **discover + add Scholar URLs by
searching**, WITHOUT ever attaching the wrong person's profile (the anti-fabrication invariant — a wrong
profile = wrong citations/research written onto a real person).

## The safety principle (non-negotiable)

Google Scholar profile pages display **"Verified email at njit.edu"** (the `#gsc_prf_ivh` element). That is a
strong, machine-checkable disambiguation signal. **Auto-write ONLY when the candidate profile shows a verified
`njit.edu` email AND the name matches.** Everything else is queued for human review, never guessed. This is the
core invariant the RAG/anti-fabrication review must verify.

## Decisions (locked with Mohammad, 2026-06-20)

- **Write policy:** auto-write strict (verified njit.edu email + name match); queue the uncertain; skip none-found.
- **Review queue:** **report-only in v1** (a written report + job log; uncertain matches actioned via the
  existing manual path / People editor). NO dashboard accept/reject UI this round.
- **Scope:** a dashboard "Discover Scholar URLs" job (Data Sources tab), scoped by college/department, reusing
  the refresh job's scope dropdown.
- **Provider:** Brave general web search (the job runs server-side as a subprocess, so it cannot use Claude's
  interactive WebSearch — it uses the programmatic Brave API). Provider-isolated/injectable.
- **Out of scope (v1):** dashboard approve/reject UI; LinkedIn/ORCID discovery (Scholar only); scheduling.

## Architecture (each piece independently testable; search/fetch INJECTED so tests need no network)

### 1. Candidate search — `v2/integration/njit_search.py` (or a sibling)
Add an **un-scoped** Brave web search (the existing `search()` forces `site:njit.edu`; discovery must reach
`scholar.google.com`). New `web_search(query) -> [url]` (same Brave client/key, no site filter; injectable
`http_get`; returns [] on error). Per person, query `"<name>" NJIT <department> Google Scholar`; keep result
URLs matching `scholar.google.com/citations`.

### 2. Verify — new `v2/core/ingestion/scholar_discovery.py`
Pure, testable functions operating on fetched profile HTML (fetch INJECTED):
- `parse_profile_identity(html) -> {name, verified_email_domain|None, affiliation_text}` — reads `#gsc_prf_in`
  (name) and `#gsc_prf_ivh` ("Verified email at njit.edu" → domain `njit.edu`).
- `name_matches(kg_name, profile_name) -> bool` — normalize both (reorder "Last, First" ↔ "First Last",
  casefold, strip middle initials/punctuation); require first+last match.
- `classify_candidate(kg_name, identity) -> "strict" | "uncertain" | "reject"`:
  - **strict**: `verified_email_domain == "njit.edu"` AND `name_matches`.
  - **uncertain**: `name_matches` but no verified njit.edu email (NJIT only in affiliation text, or none).
  - **reject**: name doesn't match.
- `discover_for_person(person, *, web_search, fetch) -> {decision, url|None, reason}` — search → fetch top N
  candidates → first **strict** wins; else best **uncertain** (for the queue); else none.

### 3. Orchestrator — `scholar_discovery.run(conn, *, web_search, fetch, org_scope, limit, delay)`
- Targets = faculty in scope (reuse `scholar.select_scholar_targets`-style org-subtree selection) **who lack a
  Scholar URL** (the inverse of the refresh job's set). Read-only selection.
- For each: `discover_for_person`. On **strict** → write the URL via `set_person_profiles`, then run the SAME
  per-person metrics+interests→areas path the refresh job uses (so a discovered person is fully populated in
  one pass). On **uncertain** → append to the review queue (no write). Polite `delay` between people.
- Returns `{scanned, written, queued, skipped, queue: [(key, name, url, reason)], errors}`. Does NOT commit
  (caller owns txn); embed handled by the CLI like the refresh job.

### 4. CLI — `scripts/discover_scholar.py` (gated; mirrors refresh_scholar.py)
`--org/--department`, `--limit`, `--delay`, `--embed`, `--commit`. Dry-run prints the proposed strict writes +
the uncertain queue (counts + a sample) and writes the full queue to `scholar_review_<scope>_<date>.csv`.
`--commit` takes a `hardened_backup`, writes strict matches, embeds new areas, and still emits the review CSV.

### 5. Job plumbing — `bot/services/jobs.py` + `v2/local_server.py` + dashboard
- `build_discover_scholar_command(...)`, `start_discover_scholar(scope, limit, embed)`, `_default_build_cmd`
  branch, `_summarize` line ("Scholar discovery complete: N written, M queued of P.").
- `POST /api/jobs/discover-scholar` (validate scope → 400, coerce limit/older, 409 busy) — mirrors
  `_api_refresh_scholar`. Reuses the existing `GET /api/jobs/scholar-scopes` (the scope dropdown already exists).
- Dashboard: add **"Discover Scholar URLs"** as a `refresh-what` option reusing the scope dropdown + a Run
  button; on completion the toast/summary reports written vs queued and points to the review CSV.

## Data flow
`button → POST /api/jobs/discover-scholar {scope,limit} → start_discover_scholar → subprocess
discover_scholar.py --commit --org … --embed → select faculty-without-Scholar in scope → per person:
Brave web_search → fetch candidate profile(s) → classify → strict: write url+metrics+areas / uncertain: queue
→ embed new areas → review CSV + summary line → dashboard.`

## Error handling / safety
- **Anti-fabrication:** strict-only auto-write (verified njit.edu email + name match); uncertain never written.
- Gated (`hardened_backup`, dry-run default, 409 one-job guard). Per-person search/fetch failure isolated
  (counted, skipped). Brave/Scholar errors → that person is skipped, run continues.
- **Budget:** ~1 Brave search/person (~200 these depts, ~500+ NJIT-wide) against the shared ~1,000/mo free
  Brave credit (same pool as the live fallback) — flagged; the `--limit` caps a run. Scholar fetch at polite delay.

## Testing (TDD)
- `parse_profile_identity`: extracts name + `njit.edu` verified email from real-shaped Scholar HTML; None when absent.
- `name_matches`: "Ghosh, Arnob" ↔ "Arnob Ghosh" true; different person false; middle-initial tolerance.
- `classify_candidate`: verified njit + match → strict; match + no verified email → uncertain; name mismatch → reject.
- `discover_for_person`: strict wins over uncertain; none-found → skip (mock web_search + fetch).
- `run`: writes only strict, queues uncertain, selection = faculty-WITHOUT-scholar in scope, distinct, isolated failures.
- `build_discover_scholar_command` arg mapping; `start_discover_scholar` dispatch; route 409 + scope validation.
- A wrong-person fixture (same name, NON-njit verified email) MUST be classified `uncertain`/`reject`, never strict.

## Goals checklist (fill at PR time)
- [ ] Brave un-scoped `web_search` (provider-isolated)
- [ ] `parse_profile_identity` / `name_matches` / `classify_candidate` (verified-njit-email gate)
- [ ] `discover_for_person` + `run` (strict-write, uncertain-queue, faculty-without-scholar selection)
- [ ] CLI `discover_scholar.py` (gated, dry-run, review CSV, --embed)
- [ ] Job plumbing (build/start/dispatch/route/summary) + dashboard option
- [ ] Anti-fabrication: strict-only auto-write, wrong-person fixture test
- [ ] Review queue = report-only (UI DEFERRED, flagged); LinkedIn/ORCID + scheduling OUT OF SCOPE (flagged)
