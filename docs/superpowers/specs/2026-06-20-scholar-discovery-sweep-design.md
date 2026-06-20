# Scholar Discovery Sweep — design (slow-drip, all-NJIT)

**Date:** 2026-06-20
**Status:** DESIGN — shape approved by Mohammad; awaiting senior-eng review + sign-off before build (TDD)
**Builds on:** the Scholar URL Discovery job (`v2/core/ingestion/scholar_discovery.py`,
`2026-06-20-scholar-url-discovery-design.md`) — reuses its tested engine; this adds a long-running,
rate-limited orchestration policy on top.

## Problem / goal

~580 NJIT faculty still lack a Scholar URL (only ECE done). Running discovery department-by-department
is fine but manual, and large bursts risk a Scholar captcha/throttle. Goal: a **long-running, polite,
resumable sweep** that drips through *all* remaining faculty-without-Scholar at a safe rate, survives
bot restarts, and never starves the shared Brave budget or hammers Scholar.

## Decisions (locked with Mohammad, 2026-06-20)

- **Rate = slow drip:** ~50/hour, achieved by a **per-fetch random jitter** (sleep ~45–100s between
  people, avg ~72s) — simpler than pre-scheduled hourly windows, same rate.
- **Resumable + terminating via an ATTEMPTED marker (review B1 fix):** a strict match writes the URL;
  **every other outcome (skip/reject/uncertain) writes a `discovery_attempted` marker** (date + decision,
  no URL). `select_discovery_targets` excludes anyone with a URL **OR** an attempt marker, so a person is
  searched **at most once per sweep** — the loop provably terminates (target set strictly shrinks),
  survives restarts (marker persisted), and never re-burns Brave on the no-match residue. Optional
  `retry_after_days` re-opens stale attempts for a future re-sweep. (Re-selecting targets ALONE was NOT
  resumable for non-strict outcomes — that was the infinite-loop blocker.)
- **Independent process:** a detached CLI; `restart.sh` doesn't touch it (only a full server shutdown
  stops it, and that's safely resumable).
- **Guardrails (the point of the spec):** a hard **Brave budget ceiling** (shared pool with the live
  fallback), **block-aware backoff** (pause hours if Scholar starts blocking), **incremental commits**
  (play nice with the live bot's WAL), one backup at start, a **cumulative review CSV**, and a progress log.

## Architecture

A new gated CLI **`scripts/discover_scholar_sweep.py`** that loops, reusing the tested per-person
engine (`discover_for_person`, `classify_candidate`, `_write_discovered`) — NOT a new classifier.

### Loop (sweep policy)
```
hardened_backup(once at start);  assert journal_mode=WAL (set defensively) and BRAVE_API_KEY present
brave_used = 0; blocked_streak = 0
while not stop_flag:
    targets = select_discovery_targets(conn, limit=CHUNK)      # excludes URL'd AND attempted → shrinks to empty
    if not targets: break                                       # everyone reachable has been attempted
    chunk_blocked = 0
    for key, name in targets:
        if stop_flag: break
        if brave_used >= BRAVE_BUDGET: stop("budget ceiling")
        brave_used += 1                                         # BEFORE the call (count even on exception)
        res = discover_for_person(conn, (key,name), web_search=web_search, fetch=fetch)
        if   res.decision == "strict":   _write_discovered(conn,key,res,today)          # writes URL+metrics+areas
        else:                            mark_attempted(conn,key,res.decision,today)    # skip/reject/uncertain/blocked
        if res.decision == "uncertain":  append_review_csv(key,name,res.url,res.reason)
        if res.decision == "blocked":    chunk_blocked += 1
        conn.commit()                                           # incremental: ms-long write txn
        log_progress(...)
        interruptible_sleep(jitter(45,100))                     # the slow drip; wakes on stop_flag
    if chunk_blocked >= BLOCK_CHUNK_LIMIT:                      # Scholar is throttling us
        blocked_streak += 1
        if blocked_streak >= MAX_BLOCKED_CHUNKS: stop("scholar blocking — resume later")
        interruptible_sleep(BACKOFF_HOURS*3600)                 # pause and resume (SIGTERM-safe)
    else:
        blocked_streak = 0
embed_new_areas()   # ONCE at the end, non-fatal (Ollama may be down) — not per chunk
```
- **B1 fix — `mark_attempted`:** writes `attrs.profiles.scholar.discovery_attempted = {date, decision}`
  (no URL) via `set_person_profiles`; `select_discovery_targets` excludes URL'd **and** attempted people
  (new `skip_attempted=True` + optional `retry_after_days`). So every person is searched **at most once**
  → the loop terminates and a re-run continues, never re-searching the residue. **This engine change also
  benefits the one-shot discovery job** (it stops re-searching dead-ends — closes the earlier deferred S4).
- **Injected** `web_search`, `fetch`, and `interruptible_sleep` (tests run instantly, assert pacing/budget/
  backoff/termination without real waits or network). The real `interruptible_sleep` loops in ≤1s steps
  checking `stop_flag` (set by the SIGTERM/SIGINT handler) so a multi-hour backoff exits a reboot cleanly.
- `CHUNK=50`, **`BRAVE_BUDGET` has NO safe default — operator MUST pass `--budget` = the current month's
  remaining Brave headroom** (the pool is shared with + concurrently drained by the live fallback; a blind
  700 can blow the ~1,000/mo ceiling and starve the user-facing search). `BLOCK_CHUNK_LIMIT=5`,
  `MAX_BLOCKED_CHUNKS=3`, `BACKOFF_HOURS=3`, jitter 45–100s — all CLI-overridable.
- **Embed ONCE at the end** (not per chunk): `embed_all` holds one write txn + needs Ollama; wrap in
  try/except, non-fatal ("run embed_all when Ollama is up"). New areas searchable at sweep end — fine.
- Sweep connection sets **`PRAGMA journal_mode=WAL` + `busy_timeout=15000`** defensively (`get_connection`
  sets neither WAL nor a long timeout) so the live bot's analytics writes interleave without a lock error.

### CLI — `scripts/discover_scholar_sweep.py`
`--db`, `--chunk 50`, **`--budget` (REQUIRED for `--commit` — current month's Brave headroom)**,
`--jitter-min/max`, `--backoff-hours 3`, `--retry-after-days`, `--commit`. **Dry-run** (default): asserts
`BRAVE_API_KEY` present, prints faculty-without-Scholar-or-attempt remaining, estimated Brave spend, and a
**best-case ETA** (~72s/person, labelled "no-block; real runs slower due to backoff") — **no fetches, no
writes**. `--commit`: `hardened_backup` once, then the loop; SIGTERM/SIGINT sets `stop_flag` → the current
person finishes, commits, exits cleanly (interruptible sleeps wake immediately). Run detached
(`nohup … &` / `disown`); appends to `logs/scholar_sweep_<date>.log` (timestamped per line) and a
single open-once `logs/scholar_review_sweep_<date>.csv`.

### Reuse (no duplication of the safety core)
The classifier, verified-njit gate, name matching, `discover_for_person`, and `_write_discovered`
(provenance tags) are **unchanged** — the sweep only changes *pacing + budget + backoff + commit cadence*.
The ONE engine addition is `mark_attempted` + `select_discovery_targets(skip_attempted=, retry_after_days=)`
(the B1 termination fix), which the one-shot job adopts too. `_write_discovered`/`mark_attempted` live in
`scholar_discovery` (importable).

### Feasibility (review S5 — set expectations honestly)
The drip rate (~50/hr) is tuned to *Brave* politeness, **not** Scholar's tighter per-IP tolerance. Realistic
outcome: a run links a few hundred, then Scholar starts captcha'ing → block-backoff fires → it gives up
after `MAX_BLOCKED_CHUNKS` having covered a fraction. **This is best-effort: resume daily; full coverage of
all ~580 ultimately needs the deferred sanctioned provider (SerpAPI).** The marker makes daily resumes
cheap (no re-search). Do NOT expect one run to finish everyone.

## Error handling / safety
- **Anti-fabrication unchanged:** same strict gate (verified njit + unique-surname/corroboration);
  uncertain → CSV, never written. The sweep cannot widen what auto-writes.
- **Brave budget ceiling** stops the sweep before it can drain the live-fallback pool.
- **Block backoff** pauses (doesn't hammer) when Scholar throttles; gives up after `MAX_BLOCKED_CHUNKS`.
- **Incremental commits + `busy_timeout`** so the live bot's analytics writes aren't blocked; one
  backup at start (retention stays at 10).
- **Resumable + SIGTERM-safe:** no state to corrupt; re-run continues. A server reboot loses nothing.
- Per-person fetch/search failure isolated (counted, skipped).

## Testing (TDD)
- **TERMINATION (the B1 regression test):** a person who is permanently `skip`/`uncertain` (never strict)
  is searched **at most once** — assert `mark_attempted` is written, they're excluded from the next chunk's
  `select_discovery_targets`, and the loop reaches `not targets` and exits (no infinite spin). This must
  fail on the old "re-select only" design.
- `select_discovery_targets(skip_attempted=True)` excludes URL'd AND attempted people; `retry_after_days`
  re-includes a stale attempt.
- strict → `_write_discovered` + commit; uncertain → CSV + `mark_attempted`; skip/reject → `mark_attempted`, no CSV.
- **Brave budget**: stops after `--budget` searches even if targets remain; counter increments before the
  call (a thrown `discover_for_person` still counts).
- **block backoff**: ≥`BLOCK_CHUNK_LIMIT` blocks in a chunk → injected sleep(BACKOFF); `MAX_BLOCKED_CHUNKS`
  consecutive → stop.
- **interruptible sleep / SIGTERM**: setting `stop_flag` mid-sleep returns immediately; the loop exits
  after committing the current person (no person left half-written).
- **jitter**: injected sleep called between people with a value in [min,max].
- dry-run: prints remaining + best-case ETA, asserts key present, takes no backup, 0 fetches (spy).
- All via injected `web_search`/`fetch`/`interruptible_sleep` — no network, no real waits.

## Out of scope (v1)
- A dashboard button / daemon / systemd unit (it's a CLI you start detached; scheduling stays deferred
  for the whole Scholar feature).
- Changing the discovery classifier or adding LinkedIn/ORCID (Scholar only).
- A sanctioned-provider (SerpAPI) swap — still owner-deferred on cost; the provider stays injectable.

## Senior-eng review outcome (2026-06-20) — folded in
Verdict was **needs-rework** (one real blocker). Folded: **B1** the loop now TERMINATES via a persisted
`discovery_attempted` marker + `select_discovery_targets(skip_attempted=)` (re-select alone re-searched the
non-strict residue forever / drained Brave — the infinite-loop blocker); **S1** `--budget` is required
(operator sets month's headroom; shared/concurrent with the live fallback) + increment before the call;
**S2** interruptible sleep so multi-hour backoff is SIGTERM-safe; **S3** embed once at end, non-fatal (not
per chunk); **S4** sweep sets `journal_mode=WAL` + `busy_timeout=15000` defensively; **S5** stated honestly
as best-effort (Scholar throttles; resume daily; full coverage needs SerpAPI). The `mark_attempted` engine
addition also fixes the one-shot job's dead-end re-search. Anti-fabrication gate untouched.

## Goals checklist (fill at PR time)
- [ ] Sweep loop reusing `discover_for_person` + `_write_discovered` (no classifier duplication)
- [ ] **B1: `mark_attempted` + `select_discovery_targets(skip_attempted=,retry_after_days=)` → provable termination + true resume**
- [ ] Per-fetch jitter (~50/hr); **interruptible** injected sleep (SIGTERM-safe backoff)
- [ ] **Required `--budget`** (no blind default) + increment-before-call
- [ ] Block-aware backoff + give-up
- [ ] Incremental commits + defensive WAL/`busy_timeout=15000`; one backup at start
- [ ] Embed ONCE at end, non-fatal
- [ ] TERMINATION regression test (permanent-skip person searched ≤ once)
- [ ] Cumulative review CSV (open-once) + timestamped progress log; dry-run = remaining + best-case ETA + key check
- [ ] Anti-fabrication gate unchanged
- [ ] Dashboard/daemon + SerpAPI + LinkedIn/ORCID — OUT OF SCOPE (flagged)
