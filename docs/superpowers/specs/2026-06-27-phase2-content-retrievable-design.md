# Phase 2 (Track B) — Make Answer-Bearing Content Retrievable — Design

**Date:** 2026-06-27
**Status:** DRAFT — brainstorming complete; awaiting expert review(s) + owner approval per the EXPERT-REVIEW HARD GATE.
**Branch:** `worktree-teacher-eval-phase2` (worktree off `feat/durable-retrieval-foundation` @ `e54b647`). Prod/main untouched; gate-branch tip frozen.
**Tracking memory:** `project_teacher_eval_phase2.md` (this is Track B; do NOT touch Track A / the gate's memory).

---

## 1. Why this exists (Phase 2 in the arc)

The main project is the confidence **GATE** (abstain-vs-answer for RAG), deliberately **paused** at the owner cutover decision. The gate's decision rides on the cross-encoder `ce_score`; re-crawling (Phase 1) and re-chunking/re-embedding (Phase 2) shift the `ce_score` distribution → shift the gate's calibrated band. **You cannot tune-and-cut-over a gate onto a corpus you are about to change.** So the sequencing is firm: **change the content first (P1+P2), tune the gate last (P3).**

- **Phase 1 (Track A, running):** source coverage — gather teacher answers → audit links vs our crawl → seed-gap report → gated crawl. *Adds content to the KB.*
- **Phase 2 (this spec):** make that answer-bearing content **actually retrievable** — fix the M2 long-page blindness (as a safe deep-fallback) + extract PDF-trapped content + re-chunk/embed the corpus. *The mechanism; the goal is content the gate can rely on.*
- **Phase 3 (Track C, later):** retrieval + gate tuning + cutover on the rebuilt corpus, oracle as calibration set. *Resumes the gate.*

**The M2 problem (one line):** a long page gets ONE semantic vector of its first ~512 tokens (the old `[:2000]`-char slice); deep content past that is invisible to the semantic leg (the keyword/FTS leg still indexes full text). ~358 items are >2000 chars (1.7%); ~60 of the biggest are enumerative grids.

---

## 2. What this builds ON (already on the branch, flag-gated, OFF, prod untouched)

The durable-foundation + answer-stack sessions already built ~90% of the chunking machine. Phase 2 **reuses, does not rebuild**:

- `model_descriptor.py` — LLM-agnostic seam; `truncate_to_tokens` replaces the `[:2000]` magic constant (NOMIC dim768/ctx2048/working512/overlap77). Honors LLM-agnostic + use-max-capacity hard lines by construction.
- `chunker.py` (token-window verbatim) + `section_chunker.py` (markdown structure-aware).
- `chunk_populate.py` — `drop_item_chunks` / `populate_item_chunks` (per-item invalidate + rechunk + embed; provider-isolated embed_fn; `content_hash = sha256(model_id + text)`).
- schema `knowledge_chunks` + `knowledge_chunk_vectors` (vec0 with `org_id` partition + `type` + `parent_id` aux → filtered KNN, verified on sqlite-vec 0.1.9).
- `embed_chunks.py` — batch chunk-embed pass (proven 6,086 items → 8,818 chunks in **52 s** on a copy).
- `retriever.py` — full chunk-KNN path behind `RETRIEVAL_CHUNKS` (`_semantic_chunks`: filtered KNN → collapse-to-parent by min-distance → dynamic over-fetch → distinct-parent gate; CE rerank on matched chunk via `_chunk_passage`).
- `vector_gc` — sweeps soft-deleted orphan vectors.

**Proven evidence on record:** deep-content semantic recall 0/25 → 18/25 (isolation) / 3→11 of 30 paraphrased (full pipeline, 3.7×). **But always-on chunking regressed the common case 84%→77%** → NO-GO for always-on → chunks repositioned as **deep-fallback only** (the decision this spec implements).

---

## 3. Scope

**IN (Phase 2):**
- **A — Deep-fallback chunk-rescue (G4):** the M2 fix delivered safely. §4.
- **B — PDF extraction:** bring PDF-trapped content in as retrievable chunks. §5.
- **C — Re-chunk/embed + invalidation completeness:** index the corpus (incl. Phase-1 new crawl) so it's complete for Phase 3. §6.

**OUT (deferred to Phase 3 / own sub-plan, owner-confirmed 2026-06-27):**
- **G3 section-structure of the always-served corpus** — risky, eval-gated hypothesis that modifies the *common* path (what regressed). Belongs where `ce_score` is re-measured + the gate re-tuned.
- **G7 office-dilution routing** — already built behind `RETRIEVAL_OFFICE_PRIOR` (gold 9/12→11/12); a ranking/precision problem (content IS found, just outranked), not retrievability; changes common-path ranking → needs the held-out office gate + ce re-measure = Phase 3.
- **The gate wiring itself** (Gate-1/Gate-2, `NOT_IN_CONTEXT` trigger, cutover) — Track C.

---

## 4. Component A — Deep-fallback chunk-rescue (the heart, G4)

### 4.1 Principle: rescue-only ⇒ cannot regress
The normal path stays exactly today's proven 84% (whole-doc vector + FTS + RRF + CE rerank). The chunk index is consulted **only when the normal path misses.** Because deep-fallback never runs on questions the normal path already answers, it is **structurally incapable of regressing the common case** — the guarantee the always-on / replace / augment / carve attempts all lacked.

### 4.2 The seam (grounded in current code)
`bot/core/message_handler.py::_rag_pipeline` already has a **primary-miss ladder**:

```
chunks    = retriever.retrieve(query)                 # normal path (unchanged)
relevance = retriever.top_relevance(base_q, chunks)   # = matched-chunk ce_score (free, already threaded)
primary_miss = (not chunks) or (relevance < LIVE_THRESHOLD)
if primary_miss:  (dead office_page tier)  ->  live njit.edu  ->  deflect
```

Deep-fallback inserts into this ladder as a new rescue step, **flag-gated** (`RETRIEVAL_DEEP_FALLBACK`, default **OFF** → prod ladder byte-identical when off):

```
if primary_miss and RETRIEVAL_DEEP_FALLBACK:
    rescue = retriever.retrieve_deep(base_q)          # chunk-KNN → collapse to parents → CE on matched chunk
    rescue_rel = retriever.top_relevance(base_q, rescue)
    if rescue and rescue_rel >= DEEP_FALLBACK_THRESHOLD:
        chunks = rescue                               # adopt the rescued PARENT pages (full, never-withhold)
        used_deep = True
# else fall through to live njit.edu -> deflect (unchanged ordering, per answer-stack §13.6 #4)
```

- **Trigger today = the existing live-fallback CE-miss signal** (`top_relevance < LIVE_THRESHOLD`). This makes deep-fallback work **now**, independent of the gate. **Track C later swaps the trigger** for the gate's `NOT_IN_CONTEXT` (the answer-stack §13.6 #4 chain: `NOT_IN_CONTEXT → deep-fallback → live → deflect`). The mechanism is the same; only the trigger source changes.
- **Ordering:** deep-fallback runs **before** live njit.edu (rescue our own KB before reaching out) and before deflect. Matches the answer-stack chain.
- **Never-withhold:** the rescue returns the **full parent page(s)** to the generator (chunks are for *finding*, parents are *served*) — the parent-document-retrieval rule, already implemented by `_semantic_chunks` collapse-to-parent.

### 4.3 New retriever entry point
Add `V2Retriever.retrieve_deep(query, ...)` (and a `V2RetrieverShim.retrieve_deep` passthrough) = the chunk-KNN rescue path, reusing the existing `_semantic_chunks` collapse-to-parent + `_chunk_passage` CE rerank, returning the same `RetrievedChunk` shape (full parent content) as the normal path. This exposes the chunk index **only as a rescue**, leaving `RETRIEVAL_CHUNKS` (the always-on replacement) **OFF** and untouched. One new method, one new flag, one new threshold; the heavy lifting already exists.

### 4.4 Calibrating `DEEP_FALLBACK_THRESHOLD`
The adopt-floor for rescued chunks. Calibrated on a copy via `eval.sh` A/B + the deep-content set (paraphrased deep Qs) so rescue *fires* on real deep questions but does NOT adopt weak fragments. Start by reusing the `LIVE_THRESHOLD`/`OFFICE_THRESHOLD` family value; tune on the deep set. The frozen-held-out discipline (don't tune on the held-out 175) is honored.

---

## 5. Component B — PDF extraction

### 5.1 Validated approach (pypdf, 8 real NJIT PDFs, 2026-06-27)
`pypdf` 6.14.2 validated on prose/FAQ/forms/calendars/tables/image-PDFs. Confirmed rules:

1. **Extract = pypdf DEFAULT mode** per page (`layout` mode is worse — injects spurious gaps).
2. **Cleanup = newline→space + collapse whitespace** (`re.sub(r"\s+", " ", t)`), pure whitespace normalization = within crawl=mechanical-only. (Do **not** "heal mid-word newlines" — it corrupts the common wrapped-word case `an\nundergraduate`→`anundergraduate`. Verified.) Rare residual on a source-side letter-split (`Self Service`→`Sel f Service`) is unavoidable from the PDF's own text layer; the source link covers it.
3. **Image-heavy detector → skip+flag (NO OCR, honest):** `chars_per_page < 200 AND bytes_per_text_char > 800`. (Caught a 3.75 MB screenshot PDF a naive empty-page check missed.)
4. **Invalid / non-PDF** (bad header / `PdfStreamError`) → skip+flag (caught an HTML-masquerading-as-PDF url).
5. **Dense numeric tables** (e.g. tuition schedules): values extract verbatim but **row boundaries degrade** (`939.00`+`1.5`→`939.001.5`) — the known grid limitation. Mitigate: treat as a grid (route off the clean-prose path, consistent with the foundation grid handling) and **always attach the source link**; never present a mangled exact figure as authoritative.

### 5.2 Module
One small, single-purpose, tested module `v2/core/ingestion/pdf_extract.py`:
- `extract_pdf_text(pdf_bytes_or_path) -> ExtractResult{text|None, status, n_pages, chars_per_page, reason}` — implements rules 1–4; returns a skip status (with reason) for image-heavy / invalid / empty.
- Provider-isolated (pypdf behind this one module — swap-able), like the embed seam.

### 5.3 Ingestion wiring (crawl-lane, mechanical-only)
PDFs are reached via the existing crawl flow: a linked PDF URL → download → `extract_pdf_text` → on success, a `knowledge_items` row tagged to the same org, `source_url` = the PDF URL, `created_by='college_crawl'` (or the office crawler), then chunked+embedded by Component C. **Recrawl = content-hash (or HTTP ETag/Last-Modified) diff**, same mechanism as HTML pages (re-extract+re-embed only if changed). Skip+flagged PDFs are listed in the crawl manifest (manifest review prunes), never silently dropped. **No usage/serving decisions in the crawler** (crawl=data-bringing-only hard line) — extraction is mechanical clean→store; how the content is served is the retrieval layer's job.

> **Dependency note:** `pypdf` is a new dependency, owner-approved 2026-06-27 (conditional on the validation above, which passed). Add to `requirements.txt`.

---

## 6. Component C — Re-chunk/embed + invalidation completeness

### 6.1 Re-chunk/embed
Run `embed_chunks.py` (the proven batch pass) to chunk+embed every active served item — including Phase-1's newly-crawled content and the new PDF rows. Gated (`hardened_backup`, dev-copy first, `--commit`), resumable, batched (52 s on a copy). The live embed is the **corpus-build hand-off into Phase 3** — it is a production write, so it runs at an owner checkpoint (batched with the rebuild, or approved standalone).

### 6.2 Invalidation completeness (the #1 ops risk)
Stale/orphaned chunks on recrawl. The durable-foundation decision (SE HIGH-2) is the architecture: writers do **not** embed inline (matches `embed_all` today); invalidation is enforced by **(a)** the `is_active` GC sweep (`vector_gc` extended to chunk vectors) + **(b)** reconcile dropping chunks of superseded/deactivated/departed items + **(c)** an **invariant test** (every active served item has chunks; no chunk/vector for an inactive/missing parent). Component C audits the writers and enforces the invariant — it does not bolt `populate_item_chunks` onto every writer.

### 6.3 The whole-doc embed truncation (`[:2000]`) still in `embed_all.py`/`embedder.py`
The chunk path uses the model descriptor; the **whole-doc** semantic path still truncates at `[:2000]`. With deep-fallback, the normal path's whole-doc vector intentionally stays a ~512-token head (deep content is rescued via chunks, not via a fatter whole-doc vector). **Decision:** route the whole-doc embed through the shared `model_descriptor.truncate_to_tokens` helper too (kills the duplicated magic constant / drift-bug; honors LLM-agnostic), but keep the working-size target — this is a mechanical de-duplication, not a behavior change. (Flagged for the reviewer: confirm no behavior change at working_size≈old slice.)

---

## 7. Data flow (end to end)

```
CRAWL (Phase 1 + PDFs)                RETRIEVAL (serving)
  HTML page  ─┐                         question
  PDF  → extract_pdf_text ─┤             │ normal path: whole-doc vec + FTS → RRF → CE rerank
              ├─→ knowledge_items        │ relevance = top_relevance (matched-chunk ce_score)
              │   (parent, full text)    ├─ relevance ≥ LIVE_THRESHOLD → ANSWER (unchanged, 84%)
  embed_chunks ─→ knowledge_chunks       └─ primary_miss →
                + chunk_vectors               retrieve_deep (chunk-KNN → parents)  [flag, A]
  invalidation: is_active GC + reconcile        ├─ rescue_rel ≥ DEEP_FALLBACK_THRESHOLD → ANSWER (deep parent)
                + invariant test                ├─ else → live njit.edu  → ANSWER
                                                └─ else → deflect honestly
```

KG/structured answers are resolved by the router **before** `_rag_pipeline` and are untouched by any of this.

---

## 8. Hard lines honored (by construction)
- **LLM-agnostic:** all sizes/limits/prefixes read from `model_descriptor`; embed + extraction provider-isolated.
- **Use-max-capacity:** chunk target = working_size (512), measured in the model's own tokenizer.
- **Verbatim / never-withhold:** chunks find, full parents serve; PDF text is mechanical-clean only; rescue returns full pages; deflect only if all tiers miss.
- **Crawl = mechanical-only, data-bringing-only:** PDF extraction strips markup/whitespace, no rewriting; no serving/gating logic in the crawler.
- **Evidence-before-claim:** every gate measured on a copy before any live write; no live-DB state asserted without proof.
- **Gated writes:** `hardened_backup` + dry-run default + `--commit`; immortal posts/judging untouched.

---

## 9. Testing strategy (TDD)
- **Component A:** unit tests for `retrieve_deep` (chunk-KNN → distinct parents → full-parent payload), the miss-ladder branch (rescue adopted iff `≥ threshold`, ordering before live), flag-off = no-op. Retrieval eval on a copy: deep-content set up (recall@5 / paraphrased deep) AND **common-case eval.sh ≥ 84% (no regression)** with the flag ON — the load-bearing gate.
- **Component B:** unit tests for `extract_pdf_text` using **the real NJIT PDFs as fixtures** (calendar 18 KB + tuition 20 KB committed as deterministic fixtures): prose→clean text, image-heavy→skip status, bad header→skip, dense table→values present. Cleanup-rule regression (wrapped-word vs letter-split).
- **Component C:** invariant test (active item ⇒ has chunks; inactive/missing ⇒ no chunk/vector); recrawl invalidation (content change → old chunks dropped, new written); GC sweep on a copy.
- **Both KB and KG:** add deep-content + PDF-sourced questions to `eval/questions.txt`; add a KG/structured regression question proving deep-fallback does not perturb structured answers (they bypass `_rag_pipeline`).
- **Judge variance:** any eval "win" must exceed the measured judge σ (≈1.8 pts); the no-regression bar is the binding one.

---

## 10. Goals checklist (maintained through build)
- [ ] **P2-G1** — `retrieve_deep` chunk-rescue method (reuses `_semantic_chunks`, full-parent payload).
- [ ] **P2-G2** — Deep-fallback wired into the `_rag_pipeline` miss-ladder, flag-gated OFF, before live; flag-off = byte-identical prod.
- [ ] **P2-G3** — `DEEP_FALLBACK_THRESHOLD` calibrated on a copy; deep recall ↑ AND eval.sh ≥ 84% with flag ON (no regression).
- [ ] **P2-G4** — `pdf_extract.py` (default extract + whitespace cleanup + image-heavy skip + header validation), tested on real-PDF fixtures.
- [ ] **P2-G5** — PDF ingestion wired into the crawl flow (mechanical-only, content-hash recrawl, manifest-flag skips), `pypdf` in requirements.
- [ ] **P2-G6** — Re-chunk/embed pass run on a copy (and live at an owner checkpoint); corpus chunk-complete.
- [ ] **P2-G7** — Invalidation completeness: GC + reconcile + invariant test; writer audit.
- [ ] **P2-G8** — Whole-doc embed de-duplicated onto `model_descriptor` (no behavior change).
- [ ] **Deferred (loud):** G3 section-structure, G7 office-dilution, the gate wiring/cutover → Track C/Phase 3.

---

## 11. Reject criteria (must hold before any cutover)
1. **No common-case regression:** `eval.sh` with deep-fallback ON ≥ 84% (within judge σ of baseline). The thing always-on chunking failed.
2. **Deep-fallback only fires on miss:** flag-off path is byte-identical to prod; flag-on changes nothing for questions the normal path already answers (proven on the common eval).
3. **PDF text is faithful + mechanical-only:** extracted text is verbatim (whitespace-normalized only); image/invalid PDFs are skip-flagged, never faked; source link always attached.
4. **Invalidation holds:** the invariant test passes on a copy after a simulated recrawl (no orphan/stale chunks).
5. **Every change gated + reversible; immortal posts/judging untouched.**

---

## 12. Build sequencing (for writing-plans)
Gated, flag-behind, eval-before-anything-live. Independent pieces first.
1. **B — `pdf_extract.py`** (pure, fixture-tested; lowest risk, no retrieval change).
2. **A — `retrieve_deep` + miss-ladder wiring** behind `RETRIEVAL_DEEP_FALLBACK` (OFF); unit tests.
3. **A — calibrate `DEEP_FALLBACK_THRESHOLD` + eval.sh A/B** on a copy (deep-recall up, common ≥84). The binding gate.
4. **C — invalidation completeness** (GC + reconcile + invariant) + the whole-doc descriptor de-dup (G8).
5. **B — PDF ingestion wiring** into the crawl flow + requirements.
6. **C — re-chunk/embed pass** on a copy; live embed deferred to the owner checkpoint (with the rebuild or approved standalone).

Each retrieval/answer-touching step: senior-eng + RAG review (HARD GATE), owner approval, TDD.

---

## 13. Open questions (for expert review)
1. **`retrieve_deep` vs `RETRIEVAL_CHUNKS`:** add a dedicated rescue method (recommended — keeps the always-on flag OFF), or reuse the existing chunk path with a "rescue-only" param? Risk of two code paths drifting vs one over-loaded path.
2. **`DEEP_FALLBACK_THRESHOLD` source:** reuse the `LIVE_THRESHOLD` value initially, or calibrate a distinct floor from the start? (Both end at "calibrate on a copy"; question is the starting point.)
3. **Grid/table PDFs:** is "route to grid handling + always link" sufficient for high-stakes tuition figures in Phase 2, or do we need a labeled grid detector now (foundation deferred it)? Recommend: sufficient for P2 (link covers it), detector stays deferred.
4. **Live re-embed timing:** fold the live chunk re-embed into the planned DB wipe+rebuild (free re-embed), or run it standalone-gated before Phase 3? Recommend: fold into the rebuild; run standalone only if Phase 3 needs it sooner.
5. **Whole-doc descriptor de-dup (G8):** confirm working_size≈old `[:2000]` slice so it's truly behavior-neutral, or does aligning to 512 tokens shift any common-case ranks? (Eval-checked.)

---

## 14. Out of scope (explicit, so nothing is silently dropped)
G3 section-structure; G7 office-dilution routing; the answer-gate (Gate-1/Gate-2) build and cutover; the curation cycle; C golden-answer tier; `catalog.njit.edu`/E crawler seeds (Phase 1's domain). These are Phase 3 / Track C / Track A. Phase 2 ends when A+B+C ship and are proven on a copy, with the live re-embed staged for the Phase-3 corpus build.
