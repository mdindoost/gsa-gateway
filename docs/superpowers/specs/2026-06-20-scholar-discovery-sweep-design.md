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
- **Resumable by construction:** targets are re-selected each chunk via `select_discovery_targets`
  (faculty without a URL) — anyone just linked drops out, so a restart continues automatically. No
  state file. Killing it loses nothing.
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
hardened_backup(once at start)
brave_used = 0; blocked_streak = 0
while True:
    targets = select_discovery_targets(conn, limit=CHUNK)      # next N without a URL (re-query → resumable)
    if not targets: break                                       # everyone reachable is done
    chunk_blocked = 0
    for key, name in targets:
        if brave_used >= BRAVE_BUDGET: stop("budget ceiling")
        res = discover_for_person(conn, (key,name), web_search=web_search, fetch=fetch)
        brave_used += 1
        if   res.decision == "strict":   _write_discovered(conn,key,res,today); conn.commit()   # incremental
        elif res.decision == "uncertain": append_review_csv(key,name,res.url,res.reason)
        elif res.decision == "blocked":   chunk_blocked += 1
        # else skip/reject
        log_progress(...)
        sleep(jitter(45,100))                                   # the slow drip
    embed_new_areas()                                           # per chunk; resumable, only new items
    if chunk_blocked >= BLOCK_CHUNK_LIMIT:                      # Scholar is throttling us
        blocked_streak += 1
        if blocked_streak >= MAX_BLOCKED_CHUNKS: stop("scholar blocking — try later")
        sleep(BACKOFF_HOURS)                                    # pause and resume
    else:
        blocked_streak = 0
```
- **Injected** `web_search`, `fetch`, and **`sleep`** (so tests run instantly and assert pacing/budget/backoff without real waits or network).
- `CHUNK=50`, `BRAVE_BUDGET≈700` (leaves headroom for the live fallback in the ~1,000/mo pool),
  `BLOCK_CHUNK_LIMIT=5`, `MAX_BLOCKED_CHUNKS=3`, `BACKOFF_HOURS=3`, jitter 45–100s — all CLI-overridable.

### CLI — `scripts/discover_scholar_sweep.py`
`--db`, `--chunk 50`, `--budget 700`, `--jitter-min/max`, `--backoff-hours 3`, `--commit`.
**Dry-run** (default): print how many faculty-without-Scholar remain (`select_discovery_targets` count),
the estimated Brave spend, and ETA at the drip rate — **no fetches, no writes**. `--commit`:
`hardened_backup` once, then the loop; SIGTERM/SIGINT → finish the current person, commit, exit cleanly.
Run detached (`nohup … &` / `disown`); logs to `logs/scholar_sweep_<date>.log`, review queue appended to
`logs/scholar_review_sweep_<date>.csv`.

### Reuse (no duplication of the safety core)
The classifier, verified-njit gate, name matching, `discover_for_person`, and `_write_discovered`
(provenance tags) are **unchanged** — the sweep only changes *pacing + budget + backoff + commit
cadence*. `_write_discovered` is imported from `scholar_discovery` (make it importable if needed).

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
- `sweep` processes targets across multiple chunks until `select_discovery_targets` is empty (mock that
  the written ones drop out → proves resumability/termination).
- strict → `_write_discovered` called + committed; uncertain → appended to the CSV; skip/reject → neither.
- **Brave budget ceiling**: stops after N searches even if targets remain.
- **block backoff**: a chunk with ≥`BLOCK_CHUNK_LIMIT` blocks triggers the injected `sleep(BACKOFF)`;
  `MAX_BLOCKED_CHUNKS` consecutive blocked chunks → stop.
- **jitter**: the injected `sleep` is called between people with a value in [min,max].
- dry-run prints remaining count + ETA, takes no backup, makes no fetch (spy asserts 0 calls).
- All via injected `web_search`/`fetch`/`sleep` — no network, no real waits.

## Out of scope (v1)
- A dashboard button / daemon / systemd unit (it's a CLI you start detached; scheduling stays deferred
  for the whole Scholar feature).
- Changing the discovery classifier or adding LinkedIn/ORCID (Scholar only).
- A sanctioned-provider (SerpAPI) swap — still owner-deferred on cost; the provider stays injectable.

## Goals checklist (fill at PR time)
- [ ] Sweep loop reusing `discover_for_person` + `_write_discovered` (no classifier duplication)
- [ ] Per-fetch jitter (~50/hr); injected sleep for tests
- [ ] Brave budget ceiling (protect the live-fallback pool)
- [ ] Block-aware backoff + give-up
- [ ] Incremental commits + `busy_timeout`; one backup at start
- [ ] Resumable (re-select targets) + SIGTERM-safe; cumulative review CSV + progress log
- [ ] Dry-run = remaining count + ETA, no fetch/write
- [ ] Anti-fabrication gate unchanged (verified-njit + unique-surname/corroboration)
- [ ] Dashboard/daemon + SerpAPI + LinkedIn/ORCID — OUT OF SCOPE (flagged)
