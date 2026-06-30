# Embed Self-Healing — Design

> Status: DESIGN (awaiting expert reviews + owner sign-off per the EXPERT-REVIEW HARD GATE).
> Date: 2026-06-30. Author: Claude (Opus 4.8), with Mohammad.
> Track: embedding/M2 infra (surfaced during the catalog Build A dev-gate).

## 1. Why

`v2/scripts/embed_chunks.py` populates the per-chunk vector index
(`knowledge_chunk_vectors`) that powers the deep-fallback tier and the answer gate.
During the catalog Build A dev-gate, running `embed_all` then `embed_chunks` back-to-back
under Ollama load left **320 chunks (active parents) with no vector** — and the run could
not recover: `embed_chunks` resumes by chunking *items that have no chunks*, but those items
already had chunks, so a plain re-run was a no-op. The only recovery was `--force`
(re-embed the entire ~19.5k-chunk corpus) or a hand-written backfill. A calm targeted
backfill then embedded all 336 holes with **0 failures**, proving the failures were transient
Ollama drops, not bad data.

**Root cause (one line):** the resumable unit is *"items without chunks"* when it must be
*"chunks without a current-model vector."* Compounding it, Phase 2 **silently skips** a chunk
whose embedding comes back `None` (no retry, no report).

**Live state is healthy** (verified): live has 0 active-parent coverage holes. The 16 live
"unvectored" chunks all have **inactive** parents — harmless orphan chunk *rows* the invariant
correctly ignores (200 such orphan rows exist from soft-deletes; only their vectors get swept,
not the rows). So this is a robustness/recovery fix, not a live-data repair.

## 2. Goals / scope

In scope (owner-approved full scope):
1. **Self-healing convergence** — a plain `embed_chunks` re-run (no `--force`) backfills any
   active-parent chunk that lacks a current-model vector.
2. **Bounded retry** on transient `None` embeddings (both scripts), via one shared helper.
   Persistent failures are **counted + reported + non-zero exit** — never silently skipped.
3. **Orphan chunk-row GC** — delete `knowledge_chunks` whose parent is inactive (the ~200 cruft
   rows), so the table stays clean forever.
4. **`embed_all` alignment** — replace its inline 2-attempt loop with the shared retry helper.

Out of scope (flagged, not dropped):
- The M2 2000-char truncation in `embed_document`/`embed_chunks` input (`text.strip()[:2000]` /
  `truncate_to_tokens`) — separate M2 work ([[project_m2_embedding]]).
- The crawl runners' "assert-after-commit" sequencing (a runner concern, not the embed scripts).

## 3. Current behavior (verified facts)

- `embed_chunks.py` Phase 1 selects `is_active=1 AND type NOT IN exclude AND id NOT IN
  (SELECT DISTINCT parent_id FROM knowledge_chunks)` (unless `--force`). Phase 2 embeds only the
  chunks it created this run (`pending`); on `Embedder.normalize(v) is None` it `continue`s
  (line ~82). Then `sweep_orphan_*_vectors` + `assert_chunk_invariant`.
- `assert_chunk_invariant` (vector_gc.py): condition 2 `_count_chunks_without_vectors` counts
  only **active-parent** chunks lacking a vector — so transient holes on active items DO fire it,
  but orphan rows (inactive parents) correctly do not.
- `embed_all.py` already has a 2-attempt loop (retries on `None` and exception) and is
  vector-level resumable (`id NOT IN (SELECT item_id FROM knowledge_vectors)`) → it already
  self-heals on re-run. This spec only *aligns* its retry to the shared helper.
- `Embedder._embed_batch` (embedder.py): one `/api/embed` call with a list input; pads/truncates
  the result 1:1 with inputs, yielding `None` for any missing/empty slot. `_embed` is the
  single-text path. `normalize` returns `None` on a `None` or zero-norm vector.

## 4. Design

### 4.1 Shared retry helper (`embedder.py`)
Add to `Embedder`:
```
def embed_document_retry(self, text, attempts=3, backoff=0.5) -> list[float] | None
```
Calls `embed_document(text)`; on `None` or exception, sleeps `backoff * attempt` and retries up
to `attempts` total; returns the normalized vector or `None` after the last attempt. (Single-text
path; reused by both scripts for the per-item retry. Batch embedding stays in `embed_chunks` for
speed; only the *retry of a failed slot* uses this single-text helper.)

### 4.2 `embed_chunks.py` — convergent Phase 2 + retry
- **Phase 1 unchanged:** chunk items that have no current-model chunk (creates chunk rows).
- **Phase 2 becomes coverage-driven:** select the embed work-set as **every active-parent chunk
  that has no row in `knowledge_chunk_vectors`** (this is the union of just-created chunks and any
  previously-failed ones):
  ```sql
  SELECT c.id, c.text, i.org_id, i.type, c.parent_id
  FROM knowledge_chunks c JOIN knowledge_items i ON i.id = c.parent_id
  WHERE i.is_active = 1
    AND NOT EXISTS (SELECT 1 FROM knowledge_chunk_vectors cv WHERE cv.chunk_id = c.id)
  ```
- Embed in batches of `BATCH` via `_embed_batch`. For any slot whose `normalize` is `None`,
  **retry that one chunk** via `embed_document_retry` (the chunk text gets the same
  `doc_prefix + truncate_to_tokens` treatment as the batch path). Write the vector if obtained.
- Track `failed` (chunk ids still `None` after retry). They are **left in place** (no vector row)
  so the next run retries them.
- **Reporting/exit:** print `vectors=<written> retried=<n> failed=<n>`. If `failed > 0`, print the
  count and **exit non-zero** (the run did not reach full coverage) — but only AFTER committing the
  vectors it *did* write, so progress is durable and a re-run shrinks the gap.

### 4.3 Orphan chunk-row GC (`vector_gc.py`)
Add:
```
def sweep_orphan_chunk_rows(conn) -> int   # DELETE FROM knowledge_chunks WHERE parent inactive
```
mirroring `_CHUNK_ORPHANS` but on the chunk ROWS (parent `is_active=0` or missing). Caller owns the
txn (no commit). `embed_chunks` calls it **before** `assert_chunk_invariant`. Fixed order:
`sweep_orphan_chunk_vectors` → `sweep_orphan_item_vectors` (existing) → **then**
`sweep_orphan_chunk_rows` (new). Sweeping vectors first removes inactive-parent chunk vectors;
sweeping rows then removes the now-vectorless inactive-parent chunk rows. End state is identical
to any order, but this one is what the plan and tests assert. (Safe: inactive-parent chunks are
never served, so deleting their rows removes pure cruft.)

### 4.4 `embed_all.py` — use the shared helper
Replace the inline `for attempt in (1, 2)` block with `emb.embed_document_retry(...)` (or the
module's function form). Behavior is equivalent but with the consistent N-attempt + backoff policy.
`_targets`/self-heal-on-re-run unchanged.

## 5. Components & interfaces (isolation)
- `v2/core/retrieval/embedder.py` — NEW method `embed_document_retry` (additive; existing methods
  unchanged).
- `v2/core/database/vector_gc.py` — NEW `sweep_orphan_chunk_rows` + a `count_orphan_chunk_rows`
  (additive; existing functions/invariant unchanged).
- `v2/scripts/embed_chunks.py` — Phase 2 rework + retry + GC call + reporting/exit.
- `v2/scripts/embed_all.py` — swap inline retry for the helper.
- `v2/tests/test_embed_self_healing.py` — NEW.

These are the embedding-pipeline files. **Collision note:** this is the M2/embedding track's
territory; built on branch `fix/embed-self-healing`, file-scoped commits, no concurrent live
embed/DB write with other agents.

## 6. Error handling
- `embed_document_retry` swallows per-attempt exceptions (timeout/conn) and retries; returns `None`
  only after the last attempt. Never raises to the caller.
- A chunk still `None` after retry → left unvectored (row kept), counted, non-zero exit. No silent
  pass: `assert_chunk_invariant` would also fire on the active-parent hole — the explicit
  count+exit makes the cause legible before the assert.
- GC deletes are scoped to inactive/missing parents only; an empty result set is a no-op.

## 7. Testing (TDD, injected fake embedder — no Ollama)
1. `embed_document_retry`: success first try; `None` then success on retry; all-`None` → `None`
   after `attempts`; exception then success; exception every time → `None` (no raise).
2. **Convergence:** run `embed_chunks` with a fake embedder that returns `None` for K chunks →
   those K are unvectored, run exits non-zero, `assert_chunk_invariant` raises. Re-run with a
   healthy fake → the K backfill, exit 0, invariant OK — **without `--force`**.
3. **Non-silent:** a residual active-parent hole yields a reported `failed>0` + non-zero exit (not
   "invariant OK").
4. `sweep_orphan_chunk_rows`: a chunk with an inactive parent is deleted; a chunk with an active
   parent is untouched; count helper agrees.
5. `embed_all` path uses the helper and still self-heals on re-run (a dropped item embeds next run).
6. Regression: existing `embed_chunks`/`vector_gc` tests still pass; a fully-healthy run reports
   `failed=0`, exit 0, invariant OK.

## 8. Rollout (gated)
DB-agnostic code change. Validate on a dev copy:
```
cp gsa_gateway.db /tmp/dev_embed.db
python3 v2/scripts/embed_chunks.py --db /tmp/dev_embed.db        # converges, invariant OK, failed=0
# fault-injection convergence is covered by the unit tests, not the live copy
```
No bot restart (embed scripts aren't in the serving path). **Coordinate any live embed with other
active agents** (single SQLite writer; `hardened_backup` rotation) — but this change itself writes
no live data; the next normal embed run simply becomes self-healing.

## 9. Goals checklist (shipped / deferred — fill at PR)
- [ ] Self-healing convergence (re-run backfills active-parent holes, no `--force`).
- [ ] Bounded retry helper, used by both scripts; persistent failure → reported + non-zero exit.
- [ ] Orphan chunk-row GC.
- [ ] `embed_all` aligned to the helper.
- [ ] DEFERRED & FLAGGED: M2 2000-char truncation; runner assert-after-commit sequencing.

## 10. Risks
- **Behavior change on partial failure:** `embed_chunks` now exits non-zero when coverage is
  incomplete (previously it asserted/raised anyway). Any wrapper that ran it with `&&` should
  treat non-zero as "re-run needed," not fatal. Documented in the runner usage.
- **GC of chunk rows:** deleting inactive-parent chunk rows is irreversible cruft-removal; bounded
  to inactive parents, covered by a test, and those rows are unreachable by serving.
- **Batch+single-retry cost:** a fully-failed batch becomes N single retries — slower under a real
  outage, but correct; backoff keeps it polite.
