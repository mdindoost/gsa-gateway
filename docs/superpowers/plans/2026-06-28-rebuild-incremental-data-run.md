# Incremental Rebuild Data-Run Implementation Plan (stack-②)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.
> **HARD GATE:** every code task = senior-eng review (+ RAG review for retrieval-touching tasks) → owner
> approval → TDD → show diff → owner sign-off. The live data run (Phase B) + cutover (Phase D) are
> **owner-run, gated, and reversible-via-backup** — the agent does NOT run them against prod.

**Goal:** Bring the proven chunk + deep-fallback + PDF stack live on the existing knowledge DB **additively
(no wipe)**, refresh content by re-crawl-in-place, and re-validate on the rebuilt corpus — without ever
touching the immortal ops data (already in a separate DB) or re-seeding manual data by stable key.

**Architecture:** Everything is additive. Chunk vectors live in a *separate* table from the existing
whole-doc `knowledge_vectors`; the deep-fallback rescue tier leaves the normal retrieval path untouched;
the whole-doc `[:2000]` embedding path is deliberately unchanged (Codex-deferred). Re-crawl uses the
existing Crawling-2.1 crawlers (ND1: no new engine), which are idempotent (content-hash) and reconcile
departures in place. No DB wipe, no atomic swap, no manual re-seed.

**Tech Stack:** Python 3.11+, SQLite + sqlite-vec (vec0), Ollama (`nomic-embed-text` 768-d, `llama3.1:8b`),
pypdf, the existing `embed_chunks` / `gc_vectors` / crawler runners.

## Global Constraints (verbatim, apply to every task)
- **Branch:** `worktree-teacher-eval-phase2` (tip `a0332bc`, on `eba7de9`). All work here; prod untouched.
- **All flags stay OFF** until Phase D. New env: `RETRIEVAL_DEEP_FALLBACK` (off), `DEEP_FALLBACK_THRESHOLD=0.30`.
- **Two DBs:** knowledge = `gsa_gateway.db` (`DATABASE_PATH`); ops = `gsa_gateway_ops.db` (`OPERATIONS_DB_PATH`).
  This plan touches ONLY the knowledge DB. Posts/judging are not in it.
- **Gated live writes:** any script writing the live DB takes a `hardened_backup(...)`, defaults to dry-run,
  requires `--commit`. Dev-copy first. Evidence-before-claim (checksum/diff/count before any state claim).
- **Crawl = mechanical-only** (strip markup; never rewrite/summarize). **Serve verbatim, never withhold.**
- **LLM-agnostic / use-max-capacity:** all chunk sizes read from the model descriptor; no magic constants.
- **GSA event_info derive lane:** GSA event posts in the ops DB are derived into this DB as `event_info`
  knowledge_items (split-ops Build-3). They are a distinct `created_by` lane; reconcile is created_by-scoped
  (won't clobber them); the embed/chunk passes MUST include them.

## What this plan DROPS vs the old wipe design (because we go incremental)
- ❌ DB wipe / greenfield. ❌ atomic mv swap. ❌ fail-closed *baseline-diff* acceptance gate (replaced by
  dev-copy validation). ❌ manual re-seed by stable key (Scholar metrics / 41 people / settings stay in place).
- ❌ **split-ops F6** (publisher/signature → `org_slug` resolve): NOT needed — org ids never renumber
  without a wipe, so `posts.org_id`-based settings reads stay correct. Remains a latent nice-to-have only.
- ❌ Plan 5 unified ingestion engine (ND1).

---

# Phase A — finish the build (code, TDD, on the branch, flags OFF)

These are the phase-2 "Task-8 prereqs" minus F6. Each is independently reviewable.

### Task A1: Wire `corpus_build_ready` into the deep-fallback miss-ladder

**Files:**
- Modify: `bot/core/message_handler.py` (the `_rag_pipeline` miss-ladder where `_deep_adopt` is called)
- Read: `v2/core/database/vector_gc.py` (`corpus_build_ready`)
- Test: `v2/tests/test_deep_fallback_ladder.py`

**Interfaces:**
- Consumes: `corpus_build_ready(conn) -> bool` (chunks exist + cover servable items + model-id/dim match).
- Produces: deep-fallback rescue is SKIPPED when `corpus_build_ready` is False (so flipping the flag on a
  DB whose chunks aren't built yet is a safe no-op, not an error).

- [ ] **Step 1: Write the failing test** — deep-fallback is skipped when corpus not built:
```python
def test_deep_fallback_skipped_when_corpus_not_ready(monkeypatch):
    # given RETRIEVAL_DEEP_FALLBACK on but corpus_build_ready() False
    # the miss-ladder must NOT call retrieve_deep / must return the non-deep path result
    ...
    assert result.is_deep is False
```
- [ ] **Step 2: Run it, verify it fails** — `pytest v2/tests/test_deep_fallback_ladder.py -k corpus_not_ready -v`
- [ ] **Step 3: Implement** — gate the deep branch behind `corpus_build_ready(conn)` (cache per-process; it's a cheap count check). Keep the existing `_deep_adopt` adopt-if-strictly-better logic unchanged.
- [ ] **Step 4: Run tests, verify pass** (this test + the existing ladder tests stay green).
- [ ] **Step 5: Commit** — `feat(deep-fallback): gate rescue on corpus_build_ready (safe flag-flip)`

### Task A2: Wire chunk-invalidation into reconcile (+ remove dead `sweep_orphan_chunks`)

**Files:**
- Modify: `v2/core/ingestion/reconcile.py` (where an item is superseded/deactivated)
- Read: `v2/core/retrieval/chunk_populate.py` (`drop_item_chunks`)
- Modify: `v2/core/database/vector_gc.py` (remove unused `sweep_orphan_chunks` if dead, OR wire it)
- Test: `v2/tests/test_chunk_invariant.py` (add a recrawl-invalidation case)

**Interfaces:**
- Consumes: `drop_item_chunks(conn, item_id)` (drops a parent's chunks + their vectors).
- Produces: when reconcile deactivates/supersedes a knowledge_item, that item's chunks are dropped so the
  next `embed_chunks` pass re-chunks the new version; no orphan chunks survive a recrawl.

- [ ] **Step 1: Write the failing test** — after reconcile deactivates item X, `knowledge_chunks` for X are gone:
```python
def test_reconcile_drops_chunks_of_deactivated_item(conn):
    # seed item + chunks; reconcile marks item is_active=0 / superseded
    # assert no chunks remain for that parent_id
    assert conn.execute("select count(*) from knowledge_chunks where parent_id=?", (xid,)).fetchone()[0] == 0
```
- [ ] **Step 2: Run it, verify it fails.**
- [ ] **Step 3: Implement** — call `drop_item_chunks` from the reconcile deactivation/supersede path. Decide `sweep_orphan_chunks`: if no caller, DELETE it + its test refs (it's dead per the phase-2 final review); else wire it into the GC sweep. Document the choice in the commit.
- [ ] **Step 4: Run tests, verify pass** (invariant + reconcile suites green).
- [ ] **Step 5: Commit** — `feat(reconcile): drop chunks on item supersede/deactivate; remove dead sweep_orphan_chunks`

### Task A3: Empty-content invariant guard

**Files:**
- Modify: `v2/scripts/embed_chunks.py` (the batch pass) and/or `v2/core/retrieval/chunk_populate.py`
- Test: `v2/tests/test_chunk_invariant.py`

**Interfaces:**
- Produces: a servable knowledge_item with empty/whitespace-only content is NOT chunked into a zero-length
  chunk (it is skipped + logged), and `assert_chunk_invariant` treats it as covered (not a coverage hole).

- [ ] **Step 1: Write the failing test** — empty-content item → 0 chunks, invariant still passes:
```python
def test_empty_content_item_skipped_not_invariant_violation(conn):
    # seed a servable item with content='   '
    populate(...)
    assert no chunk rows AND assert_chunk_invariant(conn) is True
```
- [ ] **Step 2: Run it, verify it fails.**
- [ ] **Step 3: Implement** — skip empty/whitespace content in the chunker driver; exclude such items from the coverage denominator in `assert_chunk_invariant`.
- [ ] **Step 4: Run tests, verify pass.**
- [ ] **Step 5: Commit** — `fix(chunks): skip empty-content items; invariant treats them as covered`

### Task A4: End-to-end recrawl test (the full additive cycle)

**Files:**
- Test: `v2/tests/test_recrawl_e2e.py` (new)

**Interfaces:**
- Consumes: a college/office crawler entry (smallest), `embed_chunks` batch pass, reconcile, `corpus_build_ready`.
- Produces: proof that crawl → embed_chunks → re-crawl (changed page) → reconcile → re-embed leaves the
  corpus consistent (no orphan chunks, invariant true, changed content present, deep-fallback finds it).

- [ ] **Step 1: Write the failing test** — on an in-memory/temp DB with a stubbed fetcher returning page v1
  then page v2, assert: after v1 build, chunks exist + invariant true; after v2 recrawl + re-embed, old
  chunks for the changed item are gone, new content is chunked, invariant still true, `corpus_build_ready` True.
- [ ] **Step 2: Run it, verify it fails** (test infra / wiring gaps surface here).
- [ ] **Step 3: Implement** any glue needed (likely none beyond A1–A3; this test is the gate that proves them together).
- [ ] **Step 4: Run tests, verify pass.**
- [ ] **Step 5: Commit** — `test(recrawl): e2e crawl→embed→recrawl→reconcile→re-embed invariant cycle`

### Task A5: Full-suite gate for Phase A
- [ ] Run the in-memory rebuild/phase-2/gate suite (the 153-test set) + judging 99/99; confirm 0 net-new failures vs branch baseline.
- [ ] Dispatch senior-eng + RAG reviews of the Phase-A diff (HARD GATE). Fold findings. Owner sign-off.

---

# Phase B — the gated data run (DEV COPY first, then owner-run on LIVE)

> Operational, not TDD. Every step runs on a **copy** first, verified, then owner runs `--commit` on live
> after a `hardened_backup`. All additive; reversible by restoring the backup.

### Task B1: Build the dev copy + add chunk schema
- [ ] `cp gsa_gateway.db /tmp/rebuild_dev.db` (WAL-aware: checkpoint first).
- [ ] On the copy, run the branch code's `create_all`/`create_knowledge_schema` (idempotent) → adds
  `knowledge_chunks` + `knowledge_chunk_vectors`. Verify both tables present; existing data untouched (row counts equal).

### Task B2: Embed chunks on the copy
- [ ] `DATABASE_PATH=/tmp/rebuild_dev.db python v2/scripts/embed_chunks.py` (batched). Expected ~6,086 servable
  items → ~8,818 chunks/vectors, 0 orphans, `assert_chunk_invariant` True (proven ~52s).
- [ ] Confirm `event_info` (GSA-derived) items are included in the chunk coverage.

### Task B3: GC orphan vectors on the copy
- [ ] `DATABASE_PATH=/tmp/rebuild_dev.db python scripts/gc_vectors.py --commit` → sweeps the ~891 soft-deleted
  orphan whole-doc vectors. Verify invariant clean.

### Task B4: Re-crawl in place on the copy (content refresh)
- [ ] Run the existing crawlers against the copy (gated, `--db /tmp/rebuild_dev.db`): `scripts/run_explore.py`
  (people) + `scripts/crawl_college.py` (all college/office prose entries). Idempotent; reconcile retires
  departures + drops their chunks (Task A2). Verify: row deltas sane, no manual/dashboard/scholar rows touched
  (reconcile is created_by-scoped), `verify_kg.py` passes.

### Task B5: Turn PDF on + add the 104 Phase-1 seeds (on the copy)
- [ ] Enable PDF ingestion in the crawl run (the `ingest_pdf_pages` path) → new `type='pdf'` rows.
- [ ] Append the 104 Phase-1 seed pages (`worktree-teacher-eval-phase1:seed_gap_report.md`) as crawl targets
  (ProseEntry / seed list) and crawl them. Verify new rows; image-heavy/invalid PDFs skipped+flagged (not fabricated).

### Task B6: Re-embed on the copy
- [ ] `DATABASE_PATH=/tmp/rebuild_dev.db python v2/scripts/embed_all.py` (whole-doc, new/changed) +
  `embed_chunks.py` (chunks, new/changed). Both resumable/idempotent. Verify `corpus_build_ready` True, invariant clean.

---

# Phase C — re-validate on the rebuilt (copy) corpus (the acceptance gate)

> This REPLACES the old baseline-diff acceptance gate. The bar: deep-fallback still 0-regression AND
> common-case accuracy ≥ baseline AND the Phase-3 gate still clears — measured on the rebuilt copy.

### Task C1: Re-run the deep-fallback binding gate on the rebuilt copy
- [ ] Re-run the 227-Q full-pipeline A/B (deep OFF vs ON) on `/tmp/rebuild_dev.db`. **REJECT CRITERION:**
  any prev-correct → incorrect = STOP + surface to owner. Re-freeze `DEEP_FALLBACK_THRESHOLD` if the
  corpus shift moved it (record the new value + evidence).

### Task C2: Re-run eval.sh on the rebuilt copy
- [ ] `DATABASE_PATH=/tmp/rebuild_dev.db ... bash scripts/eval.sh` (chunks/deep as the ship config).
  **REJECT CRITERION:** common-case correct% < (current 84% − judge 2σ ≈ 1.8pt). Surface the 2×2.

### Task C3: Re-run the Phase-3 gate harness on the rebuilt copy (③'s entry, not flipped here)
- [ ] `DATABASE_PATH=/tmp/rebuild_dev.db python scripts/eval_gate_shadow.py --band 0.70 --sweep` using the
  Phase-1 oracle as the calibration set. Confirm the safety case (false-deflect 0%, abstain band) still holds
  or record the refit band. (Wiring the gate to prod stays a SEPARATE ③ change — not in this plan.)

### Task C4: Phase-C gate
- [ ] All three reject criteria clear on the copy. Dispatch senior-eng + RAG review of the *measurement*
  (not just code) → owner reviews the numbers → **owner GO/NO-GO for the live run.**

---

# Phase D — live cutover (OWNER-RUN, gated, reversible)

> Only after Phase C clears and owner approves. No swap — additive writes to live behind a backup.

- [ ] **D1 (owner):** `hardened_backup(gsa_gateway.db)` → out-of-rotation snapshot recorded.
- [ ] **D2 (owner):** run Phase-B steps with `--commit` against LIVE `gsa_gateway.db` (add chunk schema →
  embed_chunks → gc_vectors → re-crawl+PDF+seeds → re-embed). Each step prints counts (evidence-before-claim).
- [ ] **D3 (owner):** merge `worktree-teacher-eval-phase2` → main; restart with `RETRIEVAL_DEEP_FALLBACK=1`
  `DEEP_FALLBACK_THRESHOLD=<frozen>`. (`RETRIEVAL_CHUNKS` always-on stays OFF/dead; office prior stays OFF.)
- [ ] **D4 (owner):** smoke-verify live — a deep-content question now answered; a normal question unchanged;
  `corpus_build_ready` True on live; `assert_chunk_invariant` True.
- [ ] **Rollback:** stop services → restore the D1 backup → `git reset` main to `eba7de9` → restart. Reversible.
- [ ] **D5:** update memory ([[project_durable_foundation]], [[project_teacher_eval_phase2]]) + `docs/REBUILD_STACK.md`
  → ② DONE. Then ③ (gate refit + prod wiring) becomes the next gated project.

---

## Goals checklist (per CLAUDE.md — shipped vs deferred)
- ✅ Chunks + deep-fallback live (the M2 deep-recall fix) — Phase B/D
- ✅ PDF content ingested — Phase B5
- ✅ 104 Phase-1 seeds added — Phase B5
- ✅ Content refreshed (re-crawl in place + departures reconciled) — Phase B4
- ✅ Orphan vectors GC'd — Phase B3
- ✅ GSA event_info derive lane chunked/embedded — Phase B2 (constraint honored)
- ✅ Re-validation on rebuilt corpus (deep-fallback 0-reg, eval ≥ baseline, gate holds) — Phase C
- 🟡 DEFERRED: Phase-3 gate **prod wiring** (③, separate gated project; harness re-run here only)
- 🟡 DEFERRED: office-dilution prior (G2) — needs held-out set, own follow-up
- 🟡 DEFERRED: full clean wipe / "spring cleaning" — optional, only if cruft proves to hurt
- ❌ DROPPED: split-ops F6 (org ids don't renumber without a wipe); Plan 5 engine (ND1)

## Decisions (LOCKED by owner 2026-06-28)
1. **Re-crawl scope = SCOPED.** Build chunks on existing content (no crawl) + add the 104 Phase-1 seeds + turn
   PDF on via a PROSE re-crawl (colleges/offices). **DEFER the `explore.py` people re-crawl** (KG data is fresh;
   re-running it = KG/reconcile risk for ~zero gain now). Rationale: chunks/deep-fallback need no crawl; PDF
   discovery requires a prose crawl anyway; people data is days-old.
2. **`DEEP_FALLBACK_THRESHOLD` = anchor `0.30`, re-confirm with evidence in Phase C** (C1 re-runs the 227-Q A/B;
   move it only if the corpus shift demands, recording the new value + why). Not kept blindly, not guessed.
3. **Whole-doc `[:2000]` = LEAVE UNTOUCHED.** It ≈512 tok = nomic's quality sweet spot (ceiling 2048 tok, but a
   bigger single vector blurs → hurts the common case). Depth is handled by CHUNKS (many 512-tok vectors), not a
   bigger whole-doc vector. (Separate: llama generation `num_ctx=16384` is already handled — not this limit.)

## Session execution mode (owner grant 2026-06-28)
Owner put Claude OUT of the loop for this session: drive to the end autonomously; **Codex + self-review replace
the owner-in-loop EXPERT-REVIEW hard gate**; Claude is the decision-maker; escalate ONLY if genuinely undecided
or a reject-criterion trips. Safety hard-lines STILL apply (hardened_backup, dev-copy-first, evidence-before-claim).
**The ONE retained human checkpoint = Phase D live production cutover** (user-facing, hard to fully reverse) —
Claude pauses there with validated evidence for a one-word go.
