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

### 4.1 Shared retry-POLICY helper (`embedder.py`)
A **module-level free function** in `embedder.py` (NOT a method — so `embed_all`, which uses no
`Embedder` instance, can import it directly). A pure retry/backoff wrapper around any embed callable
— it does NOT bake in prefix/truncation, so each caller keeps its own embed semantics (resolves the
C1/S1 truncation mismatch + the S3 embed_all-semantics concern):
```
def embed_with_retry(call, attempts=3, backoff=0.5) -> list[float] | None
    # call: () -> list[float] | None   (a RAW embedding call; no normalization)
    # returns the RAW vector from the first non-None attempt, or None after `attempts`.
    # Catches per-attempt exceptions (timeout/conn reset), sleeps backoff*attempt, retries.
    # Never raises. Normalization is the CALLER's job (one write site) — S2.
```
Callers:
- `embed_chunks` retries the **exact prepared `embed_input`** the batch used:
  `embed_with_retry(lambda: emb._embed(embed_input))` — same `d.doc_prefix + d.truncate_to_tokens`
  as the batch, so a retried chunk's vector is identical to its siblings'.
- `embed_all` keeps its own `embed_document` (incl. the out-of-scope `[:2000]` M2 truncation):
  `embed_with_retry(lambda: embed_document(row["search_text"]))`. Only the retry/backoff POLICY is
  shared; embed_all's embed semantics are unchanged.
Both return a RAW vector; each caller normalizes once at its single write site
(`Embedder.normalize` / `_store_vector`).

### 4.2 `embed_chunks.py` — convergent Phase 2 + retry
- **Fast-fail:** call `emb.health_check()` at the top; abort before touching the DB if Ollama/model
  is down (mirrors embed_all; avoids grinding ~19.5k chunks against a dead server — G/N1).
- **Phase 1 unchanged:** chunk items that have no current-model chunk (creates chunk rows).
- **Phase 2 becomes coverage-driven:** the embed work-set is **every active-parent chunk that has
  no row in `knowledge_chunk_vectors`** — the union of just-created chunks and any previously-failed
  ones. This `WHERE` is the **exact complement of invariant condition 2**
  (`_count_chunks_without_vectors`), which is what makes a healthy re-run drive condition 2 → 0
  (convergence-by-complement). It is intentionally **NOT model_id-scoped**: scoping it would break
  the complement property; a stale-model chunk that gets a current-model vector is still caught by
  condition 4 (stale `model_id`) and condition 1 (served item needs a current-model chunk), so
  `--force` remains the model-change path and nothing is masked (per both reviews).
  ```sql
  SELECT c.id, c.text, i.org_id, i.type, c.parent_id
  FROM knowledge_chunks c JOIN knowledge_items i ON i.id = c.parent_id
  WHERE i.is_active = 1
    AND NOT EXISTS (SELECT 1 FROM knowledge_chunk_vectors cv WHERE cv.chunk_id = c.id)
  ORDER BY c.id
  ```
- Embed in batches of `BATCH`. **Wrap the batch call** so a batch-level exception (connection reset
  under load — C2) degrades to `vecs = [None] * len(batch)` instead of crashing. For any slot whose
  raw vector is `None`, **retry that one chunk** via `embed_with_retry(lambda: emb._embed(embed_input))`
  with the *same* `embed_input` the batch built. Normalize once and write the vector if obtained.
- Track `failed` (chunk ids still `None` after retry); leave them in place (no vector row) so the
  next run retries them. **Outage-abort (N1):** if an entire batch is still all-`None` after
  per-slot retries, treat it as an outage — stop, commit the vectors already written, report, and
  exit non-zero (re-runnable) rather than grinding every remaining batch. On the abort path the GC
  sweeps + `assert_chunk_invariant` are SKIPPED (cleanup and the coverage gate belong to a run that
  actually reached the end; orphans harmlessly persist to the next run). The timeout for a per-slot
  retry is `_embed`'s default (30s) vs the batch's 60s — immaterial for single texts.
- **GC + reporting/exit order:** Phase 2 (per-batch commit) → GC sweeps (§4.3) + commit →
  `if failed > 0:` print the report and **`sys.exit(1)`** (BEFORE the invariant assert, so the cause
  is legible) `else:` `assert_chunk_invariant`. Report line includes the **starting** active-parent
  hole count, `vectors=<written> retried=<n> failed=<n>`, and the `sweep_orphan_chunk_rows` count
  (N3 — visible shrink + auditable GC).
- **Why exit-non-zero matters (RAG #1):** serving gates the ENTIRE deep-fallback tier on
  `corpus_ready()` → `assert_chunk_invariant` (`retriever_shim.py`), evaluated ONCE and cached per
  process. A holed corpus that an operator/wrapper then *restarts* on would flip `corpus_ready` to
  False → deep-fallback silently OFF → the answer-gate routes `NOT_IN_CONTEXT` to live→deflect
  (conservative: more deflection, never a wrong/fabricated answer). The non-zero exit is what stops
  a restart on an incomplete corpus.

### 4.3 Orphan chunk-row GC (`vector_gc.py`)
Add:
```
def sweep_orphan_chunk_rows(conn) -> int   # DELETE FROM knowledge_chunks WHERE parent inactive
```
mirroring `_CHUNK_ORPHANS` but on the chunk ROWS (parent `is_active=0` or missing); add a matching
`count_orphan_chunk_rows` (used by the report). Caller owns the txn (no commit).
**Bonus honesty (RAG #3):** condition 4 (`_count_stale_model_chunks`) counts `model_id != descriptor`
over ALL chunks, not just active-parent ones. Today's ~200 orphan rows pass only because they carry
the current model_id; after a future model change those unservable rows would FALSE-FIRE condition 4.
Sweeping the rows before the assert removes that false-fire source, so condition 4 reflects only
servable chunks — the GC makes the invariant strictly more honest. `embed_chunks` calls it **before** `assert_chunk_invariant`. Fixed order:
`sweep_orphan_chunk_vectors` → `sweep_orphan_item_vectors` (existing) → **then**
`sweep_orphan_chunk_rows` (new). Sweeping vectors first removes inactive-parent chunk vectors;
sweeping rows then removes the now-vectorless inactive-parent chunk rows. End state is identical
to any order, but this one is what the plan and tests assert. (Safe: inactive-parent chunks are
never served, so deleting their rows removes pure cruft.)

### 4.4 `embed_all.py` — share the retry POLICY only (decision, not a hedge)
Replace the inline `for attempt in (1, 2)` block with
`embed_with_retry(lambda: embed_document(row["search_text"]))`. embed_all keeps its OWN module-level
`embed_document`/`_post_embed`/`normalize` and its `_store_vector` (which normalizes) — only the
retry/backoff policy is shared (S3). embed_all instantiates no `Embedder`; it imports the
`embed_with_retry` function (which lives in `embedder.py` but is a free function taking a callable,
so no class instance is needed). `embed_with_retry` returns the RAW vector and `_store_vector`
normalizes once (no double-normalize — S2). The intentionally-retained `[:2000]` truncation stays
(out of scope, M2). `_targets`/self-heal-on-re-run unchanged.

## 5. Components & interfaces (isolation)
- `v2/core/retrieval/embedder.py` — NEW module-level free function `embed_with_retry(call, attempts,
  backoff)` (a retry-policy wrapper around any raw embed callable; additive; existing `Embedder`
  methods unchanged).
- `v2/core/database/vector_gc.py` — NEW `sweep_orphan_chunk_rows` + a `count_orphan_chunk_rows`
  (additive; existing functions/invariant unchanged).
- `v2/scripts/embed_chunks.py` — Phase 2 rework + retry + GC call + reporting/exit.
- `v2/scripts/embed_all.py` — swap inline retry for the helper.
- `v2/tests/test_embed_self_healing.py` — NEW.

These are the embedding-pipeline files. **Collision note:** this is the M2/embedding track's
territory; built on branch `fix/embed-self-healing`, file-scoped commits, no concurrent live
embed/DB write with other agents.

## 6. Error handling
- `embed_with_retry` swallows per-attempt exceptions (timeout/conn) and retries; returns `None`
  only after the last attempt. Never raises to the caller.
- **Batch-level exception (C2):** the `_embed_batch` call is wrapped; a connection reset under load
  degrades the whole batch to `[None]*len(batch)` → each slot funnels into per-slot retry, so a
  transient outage produces the designed "report + non-zero exit," never a traceback.
- A chunk still `None` after retry → left unvectored (row kept), counted, non-zero exit. No silent
  pass: `assert_chunk_invariant` would also fire on the active-parent hole — the explicit
  count+exit makes the cause legible before the assert.
- Health-check fails / Ollama down at start → exit before any DB write (fast-fail).
- GC deletes are scoped to inactive/missing parents only; an empty result set is a no-op.

## 7. Testing (TDD, injected fake embedder — no Ollama)
1. `embed_with_retry`: success first try (no extra calls); `None` then success on retry; all-`None`
   → `None` after exactly `attempts`; exception then success; exception every time → `None`
   (never raises); returns the RAW vector (caller normalizes).
2. **Convergence (the headline):** run `embed_chunks` with a fake embedder that returns `None` for
   K chunks **in BOTH the batch AND the single-retry path** (else the retry heals them in-run and
   the unvectored state never occurs — test-design gap flagged by review). Assert: those K are
   unvectored, the run exits non-zero, `assert_chunk_invariant` would raise. Re-run with a healthy
   fake → the K backfill, exit 0, invariant OK — **without `--force`**.
3. **Batch-exception degrade (C2):** a fake whose `_embed_batch` RAISES (connection reset) does not
   crash the run — it degrades to per-slot retry; persistent failure → reported `failed>0` +
   non-zero exit (not a traceback, not a silent skip).
4. **Retry-input consistency (C1):** assert the string passed to the single-retry embed call is
   byte-identical to the `embed_input` the batch built (same `doc_prefix` + `truncate_to_tokens`),
   so a retried chunk's vector matches its siblings'.
5. **No-masking (condition 4):** a stale-`model_id` chunk gets a current-model vector via the
   model-blind select, yet `assert_chunk_invariant` STILL raises on condition 4 (proves the model
   change isn't masked).
6. `sweep_orphan_chunk_rows`/`count_orphan_chunk_rows`: inactive-parent chunk rows deleted,
   active-parent untouched; and the GC removes a stale-`model_id` orphan row that would otherwise
   false-fire condition 4 after a model change.
7. `embed_all`: uses `embed_with_retry`, `_store_vector` normalizes once (no double-normalize bug),
   still self-heals on re-run (a dropped item embeds next run).
8. **Health-check fast-fail:** `embed_chunks` exits early (no DB writes) when the embedder
   health-check fails.
9. Regression: existing `test_vector_gc.py`, `test_chunk_invariant.py`, `test_chunk_populate.py`,
   `test_chunk_vectors.py`, `test_chunk_retrieval.py`, `test_deep_fallback_ladder.py` still pass; a
   fully-healthy run reports `failed=0`, exit 0, invariant OK.

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
- [ ] Self-healing convergence of **vector holes** (re-run backfills active-parent unvectored chunks,
      no `--force`). Convergence-by-complement; model changes still need `--force` (condition 4 backstop).
- [ ] Bounded retry policy (`embed_with_retry`), used by both scripts; persistent failure AND
      batch-level exception (C2) → reported + non-zero exit (never silent, never a crash).
- [ ] Retry re-sends the exact batch `embed_input` (C1); normalize once at the write site (S2).
- [ ] Health-check fast-fail + outage-abort in `embed_chunks`.
- [ ] Orphan chunk-row GC (+ makes condition 4 more honest).
- [ ] `embed_all` shares the retry policy only (own embed semantics + `[:2000]` retained).
- [ ] Report includes starting hole count, vectors/retried/failed, GC count.
- [ ] DEFERRED & FLAGGED: M2 2000-char truncation; runner assert-after-commit sequencing;
      `populate_item_chunks` (recrawl path) shares the same silent-skip-on-None pattern — same
      retry fix should be applied there as a follow-up (out of scope here).

## 10. Risks
- **Behavior change on partial failure:** `embed_chunks` now exits non-zero when coverage is
  incomplete (previously it asserted/raised anyway). Any wrapper that ran it with `&&` should
  treat non-zero as "re-run needed," not fatal. Documented in the runner usage.
- **GC of chunk rows:** deleting inactive-parent chunk rows is irreversible cruft-removal; bounded
  to inactive parents, covered by a test, and those rows are unreachable by serving.
- **Batch+single-retry cost:** a fully-failed batch becomes N single retries — slower under a real
  outage, but correct; backoff keeps it polite, and the outage-abort (§4.2) caps the grind.
- **Early `sys.exit` on `failed>0` defers non-coverage diagnostics (N2):** you won't learn about a
  concurrent condition-4 (stale model) or condition-5 (dim) violation until coverage is healed.
  Acceptable — coverage must converge first anyway, and a model/dim change is the `--force` path.
