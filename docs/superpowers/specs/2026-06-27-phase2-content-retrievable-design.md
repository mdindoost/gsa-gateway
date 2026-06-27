# Phase 2 (Track B) — Make Answer-Bearing Content Retrievable — Design

**Date:** 2026-06-27 · **Rev 2** (folds the Codex hard-gate expert review: GO-WITH-CHANGES, 20 findings — all accepted).
**Status:** DRAFT — expert review folded; awaiting owner sign-off per the EXPERT-REVIEW HARD GATE.
**Branch:** `worktree-teacher-eval-phase2` (worktree off `feat/durable-retrieval-foundation` @ `e54b647`). Prod/main untouched; gate-branch tip frozen.
**Tracking memory:** `project_teacher_eval_phase2.md` (Track B; do NOT touch Track A / the gate's memory).

---

## 1. Why this exists (Phase 2 in the arc)

The main project is the confidence **GATE** (abstain-vs-answer for RAG), deliberately **paused** at the owner cutover decision. The gate's decision rides on the cross-encoder `ce_score`; re-crawling (Phase 1) and re-chunking/re-embedding (Phase 2) shift the `ce_score` distribution → shift the gate's calibrated band. **You cannot tune-and-cut-over a gate onto a corpus you are about to change.** Sequencing is firm: **change the content first (P1+P2), tune the gate last (P3).**

- **Phase 1 (Track A, running):** source coverage — gather → audit links → seed-gap report → gated crawl. *Adds content.*
- **Phase 2 (this spec):** make that content **retrievable** — fix M2 long-page blindness (safe deep-fallback) + extract PDF-trapped content + re-chunk/embed. *The mechanism.*
- **Phase 3 (Track C, later):** retrieval + gate tuning + cutover on the rebuilt corpus, oracle as calibration set. *Resumes the gate.*

**The M2 problem:** a long page gets ONE semantic vector of its first ~512 tokens (the old `[:2000]`-char slice); deep content past that is invisible to the semantic leg (keyword/FTS still indexes full text). ~358 items >2000 chars (1.7%); ~60 biggest are enumerative grids.

---

## 2. What this builds ON (already on the branch, flag-gated, OFF, prod untouched)

Reuse, do not rebuild: `model_descriptor.py` (LLM-agnostic seam; `truncate_to_tokens` replaces `[:2000]`); `chunker.py` + `section_chunker.py`; `chunk_populate.py` (per-item invalidate+rechunk+embed; `content_hash = sha256(model_id + text)`); schema `knowledge_chunks` + `knowledge_chunk_vectors` (vec0 org/type/parent aux → filtered KNN); `embed_chunks.py` (batch, 6,086→8,818 chunks in 52 s on a copy); `retriever._semantic_chunks` (filtered KNN → collapse-to-parent by min-distance → dynamic over-fetch → distinct-parent gate; CE on matched chunk via `_chunk_passage`); `vector_gc`.

**Proven:** deep recall 0/25→18/25 (isolation), 3→11/30 paraphrased (full pipeline, 3.7×). **But always-on chunking regressed the common case 84%→77%** → chunks repositioned to **deep-fallback only** (this spec).

---

## 3. Scope

**IN:** A — deep-fallback chunk-rescue (the M2 fix, §4); B — PDF extraction (§5); C — re-chunk/embed + invalidation completeness (§6).

**OUT (deferred, owner-confirmed):** G3 section-structure; G7 office-dilution routing; the gate (Gate-1/2, `NOT_IN_CONTEXT` trigger, cutover) → Track C/Phase 3. **Whole-doc `[:2000]` de-dup (former G8) DEFERRED** — it changes the common-path embedding (`[:2000]` chars ≠ 512 tokens, finding #13); out of Phase 2 to keep the common path untouched. **Grid/table handling is NOT fully deferrable** because Phase 2 ingests tuition/fee/deadline PDFs (finding #18) → handled via the degraded-table flag (§5), not deferred.

---

## 4. Component A — Deep-fallback chunk-rescue (the heart)

### 4.1 The no-regression CONTRACT (revised — was an overclaim, findings #1/#6/#20)
Earlier framing ("rescue-only ⇒ *structurally* cannot regress") is **withdrawn**. It is false: on a `primary_miss` where live-fallback is off/fails, the pipeline **still generates from the original chunks** (`message_handler.py:677`), and some low-`ce` questions are answered *correctly today*. Replacing those chunks with rescued ones could change a correct answer. The real protection is a **measured + adopt-if-better** contract:

1. **Adopt-only-if-strictly-better:** deep-rescue replaces the working `chunks` **iff** `rescue_relevance > current_relevance` **AND** `rescue_relevance ≥ DEEP_FALLBACK_THRESHOLD`. A rescue that isn't clearly better than what's already there is discarded → a currently-answered miss keeps its existing (better-or-equal) chunks.
2. **Measured on the FULL answer pipeline** (not retrieval-only), `RETRIEVAL_DEEP_FALLBACK=ON`, with **per-question regression tracking**: trace whether deep fired/adopted per question.
3. **Binding reject (split, §11):** *no previously-correct common-case answer may become incorrect* (per-question), deep-set must improve materially; aggregate ≥84 is secondary, not sufficient.
4. **Flag-off:** the retrieval **control flow** is unchanged when `RETRIEVAL_DEEP_FALLBACK=OFF` (NOT "byte-identical prod" — corpus/PDF/chunk-table changes are separate, tested under §5/§6, finding #20).

### 4.2 The seam + exact ladder (findings #2/#3)
`_rag_pipeline` already has a primary-miss ladder. Deep-fallback inserts at a **pinned** position:

```
chunks    = retriever.retrieve(query)                       # normal path (unchanged)
relevance = retriever.top_relevance(base_q, chunks)         # matched-chunk ce_score
primary_miss = (not chunks) or (relevance is not None and relevance < LIVE_THRESHOLD)
#   relevance is None  -> NOT a miss (conservative no-regress; do not deep-rescue, finding #3)

# PINNED LADDER:  primary KB  ->  office tier (currently 0 rows/dead)  ->  DEEP-FALLBACK  ->  live njit.edu  ->  deflect
if primary_miss and not used_office and RETRIEVAL_DEEP_FALLBACK and corpus_build_ready:
    rescue     = retriever.retrieve_deep(base_q, query_vec=primary_qvec)   # reuse the embedding (finding #15)
    rescue_rel = retriever.top_relevance(base_q, rescue)
    if rescue and rescue_rel is not None and rescue_rel >= DEEP_FALLBACK_THRESHOLD \
            and (relevance is None or rescue_rel > relevance):             # adopt-if-better (#1)
        chunks = rescue; used_deep = True                                   # full PARENT pages (never-withhold)
# else fall through to live -> deflect (unchanged)
```

- Deep runs **after** the existing office tier, **before** live (no office/live regression, finding #2; matches the answer-stack `NOT_IN_CONTEXT → deep → live → deflect` chain).
- **Track C** later swaps the trigger (`primary_miss`) for the gate's `NOT_IN_CONTEXT`. Same mechanism; only the trigger source changes.
- Full **parent** pages served (chunks find, parents serve — never-withhold).

### 4.3 `retrieve_deep` over a shared core (finding #4)
Add `V2Retriever.retrieve_deep(query, query_vec=None, ...)` + a `V2RetrieverShim.retrieve_deep` passthrough. **Factor retrieval through ONE internal `_retrieve(..., semantic_mode="whole_doc" | "chunk")`** so `retrieve()` (whole_doc) and `retrieve_deep()` (chunk) share allowed-IDs, boosts, CE rerank, hydration, grouping, and source-url behavior — only the semantic leg differs. This keeps the always-on `RETRIEVAL_CHUNKS` path OFF and avoids logic drift between two near-identical retrieve paths.

### 4.4 Threshold calibration (finding #5)
`DEEP_FALLBACK_THRESHOLD` is calibrated as a **distinct** floor — NOT inherited from `LIVE_THRESHOLD` (chunk-passage CE is not calibrated against whole-doc CE; short clean fragments can over-score while the full parent is still not answerable). Method: build positive deep-content queries + hard negatives/near-misses; plot precision / recall / **adoption rate** vs threshold; **select by false-adoption cost** (not recall alone); **freeze before the held-out eval**; report adoption count on the common-case eval. Honors the frozen-held-out discipline (don't tune on the 175).

### 4.5 Perf + diversity guards (findings #15/#16)
- Thread the primary path's `query_vec` into `retrieve_deep` (no second embed); cap deep concurrency/rate; **log latency separately** for primary / deep-KNN / CE.
- vec0 `_VEC_KNN_MAX=4096` is a hard ceiling. Add metrics + tests: distinct parents recovered, cap-reached, adoption-when-cap-reached, org/type-filtered deep retrieval. **If the cap is hit with low parent diversity, refuse adoption** (don't serve a pool dominated by one long PDF/grid) and fall through to live.

---

## 5. Component B — PDF extraction

### 5.1 Validated approach (pypdf, 8 real NJIT PDFs, 2026-06-27)
1. **Extract = pypdf DEFAULT mode** per page (`layout` worse).
2. **Cleanup = newline→space + collapse whitespace** = **text-preserving mechanical normalization only** (finding #8; not byte-for-byte "verbatim" but no token-joining, no inferred punctuation, no word repair, no row reconstruction — tests enforce this). Do **not** heal mid-word newlines (corrupts `an\nundergraduate`→`anundergraduate`, verified). Rare source-side letter-split (`Self Service`→`Sel f Service`) is from the PDF's own text layer; link covers it.
3. **Per-page stats + status codes (finding #9):** total pages, near-zero-char pages, median chars/page, bytes/text ratio → status ∈ `{ok, empty, image_heavy, mixed_low_text, invalid}`. `image_heavy` = chars/page < 200 AND bytes/text-char > 800; `mixed_low_text` (some pages image-only, others text) → **flag for review even if not skipped**; `invalid` = bad header / `PdfStreamError`. No OCR.
4. **Dense numeric tables (findings #7/#18) → `pdf_table_degraded=true`** metadata that **affects serving**: values extract verbatim but row boundaries can merge (`939.00`+`1.5`→`939.001.5`). Honoring **never-withhold**, we still serve the content, but the flag forces (a) the source link in the answer and (b) a generator instruction "this is a degraded table extract — direct the user to the linked source for exact figures." We do **not** present a mangled figure as a clean authoritative number, and do **not** rely on "always link" alone.

### 5.2 Module
`v2/core/ingestion/pdf_extract.py` — one small, single-purpose, tested, provider-isolated module:
`extract_pdf_text(pdf_bytes_or_path) -> ExtractResult{text|None, status, n_pages, median_chars_per_page, bytes_per_text_char, table_degraded, reason}` (rules 1–4). pypdf lives behind this module only (swap-able).

### 5.3 Ingestion wiring (crawl-lane, mechanical-only) + PDF-is-a-common-path-change (findings #12/#19)
Linked PDF URL → download → `extract_pdf_text` → on `ok`/`mixed_low_text` a `knowledge_items` row, **distinct `type='pdf'`** (explicit inclusion policy, so PDF rows are a controlled corpus addition), `source_url` = the PDF URL, `created_by` = the crawler. **Recrawl = content-hash / ETag / Last-Modified diff.** `image_heavy`/`invalid` → manifest-flagged, never silently dropped. No serving/gating in the crawler (crawl=mechanical, data-bringing-only).
**Adding PDF rows changes BM25 / whole-doc retrieval → it is a common-path change in its own right.** Evaluate it as such: eval.sh with PDFs **added, deep OFF** (proves ingestion alone didn't pollute primary retrieval) THEN deep **ON**. Degraded-table PDFs serve under the §5.1.4 safeguards.

> **Dependency:** `pypdf` (new dep) owner-approved 2026-06-27 conditional on validation (passed). Add to `requirements.txt`.

---

## 6. Component C — Re-chunk/embed + invalidation completeness

### 6.1 Re-chunk/embed + build-version gating (finding #11)
Run `embed_chunks.py` (gated, dev-copy first, `--commit`, resumable, ~52 s) to chunk+embed every active served item incl. Phase-1 crawl + new PDF rows. **Build-version gating:** write crawl changes → build chunks into the current build → reconcile → run invariant → **only then is `corpus_build_ready` true and deep-fallback eligible** (else deep stays OFF). Closes the stale-window between a crawl write and the embed pass. The live embed is a production write at an owner checkpoint (with the rebuild, or approved standalone).

### 6.2 Invalidation completeness — invariant (findings #10/#11)
Architecture (durable-foundation SE HIGH-2): writers do NOT embed inline; enforced by GC + reconcile + an **invariant test** asserting:
- every active served item has chunks **for the current `descriptor.id`**;
- every chunk has a vector; every vector has a live parent;
- no chunks/vectors for inactive/missing parents;
- **chunk/vector dimension matches the descriptor**; duplicate chunk sets for old `model_id`s are removed/ignored (model-version invalidation, finding #10).
`vector_gc` extended to chunk vectors; reconcile drops chunks of superseded/deactivated/departed items; writer audit enumerates the sites.

---

## 7. Data flow

```
CRAWL (Phase 1 + PDFs)                     RETRIEVAL (serving)
  HTML ─┐                                   question
  PDF → extract_pdf_text(status,degraded)─┤  normal path: whole-doc vec + FTS → RRF → CE rerank
        ├─→ knowledge_items (type='pdf')   │  relevance = top_relevance (matched-chunk ce)
  embed_chunks → chunks + chunk_vectors    ├─ relevance ≥ LIVE_THRESHOLD → ANSWER (unchanged, 84%)
  reconcile + GC + invariant               │  (relevance None → NOT a miss)
  → corpus_build_ready                      └─ primary_miss → office(dead) →
                                                 retrieve_deep (query_vec reused) →
                                                   adopt iff rescue_rel ≥ T AND > relevance → ANSWER (deep parent)
                                                 → else live njit.edu → else deflect
```
KG/structured answers resolve in the router **before** `_rag_pipeline` — untouched (regression-tested, §9).

---

## 8. Hard lines honored
LLM-agnostic (sizes/prefixes from `model_descriptor`; embed + pypdf provider-isolated); use-max-capacity (working_size 512, own tokenizer); verbatim/never-withhold (chunks find/parents serve; degraded tables served-with-safeguards not withheld; deflect only if all tiers miss); crawl=mechanical-only/data-bringing-only (text-preserving normalization; no serving logic in crawler); evidence-before-claim (every gate measured on a copy); gated reversible writes; immortal posts/judging untouched.

---

## 9. Testing strategy (TDD)
- **A:** unit — `_retrieve(semantic_mode)` parity (whole_doc==today), `retrieve_deep` (distinct parents, full-parent payload, query_vec reuse, refuse-adopt on low-diversity cap), miss-ladder branch (adopt iff `≥T AND >relevance`; None≠miss; ordering office→deep→live), flag-off control-flow unchanged. Eval on a copy: deep-content recall@5/paraphrased ↑ AND **per-question common-case no-regression** (binding) with flag ON.
- **B:** unit — `extract_pdf_text` on real-PDF fixtures (calendar 18 KB + tuition 20 KB committed + checksums/metadata, finding #17): prose→clean, image_heavy→skip, mixed_low_text→flag, invalid→skip, dense table→degraded flag; cleanup-rule regression (wrapped-word vs letter-split; no token-joining/punctuation-inference).
- **C:** invariant test (incl. descriptor.id/dim + model-version dedup); recrawl invalidation (content change → old chunks dropped); GC sweep; build-version gating (deep ineligible until build ready).
- **Common-path-from-ingestion:** eval.sh with PDFs added, deep OFF (P2-G9), then ON.
- **Both KB and KG:** add deep-content + PDF-sourced Qs to `eval/questions.txt`; add a KG/structured regression Q proving deep-fallback doesn't perturb structured answers.
- **Judge variance:** any "win" must exceed judge σ (≈1.8 pts); the per-question no-regression bar is binding.
- **Perf:** latency logged separately (primary / deep-KNN / CE); cap-reached + diversity metrics asserted.

---

## 10. Goals checklist
- [ ] **P2-G1** — `retrieve_deep` via shared `_retrieve(semantic_mode)` core (full-parent payload, query_vec reuse).
- [ ] **P2-G2** — Deep-fallback wired into the pinned ladder (office→deep→live), flag OFF, adopt-if-better, None≠miss, build-gated.
- [ ] **P2-G3** — Distinct `DEEP_FALLBACK_THRESHOLD` calibrated (false-adoption cost, frozen); deep recall ↑ AND per-question common-case no-regression with flag ON.
- [ ] **P2-G4** — `pdf_extract.py` (default extract + text-preserving cleanup + per-page status codes + degraded-table flag), fixture-tested.
- [ ] **P2-G5** — PDF ingestion wired (mechanical, `type='pdf'`, content-hash recrawl, manifest-flag skips, degraded-table serving safeguards); `pypdf` in requirements.
- [ ] **P2-G6** — Re-chunk/embed pass on a copy (live at owner checkpoint); corpus chunk-complete; build-version gating.
- [ ] **P2-G7** — Invalidation completeness: GC + reconcile + invariant (descriptor id/dim, model-version dedup); writer audit.
- [ ] **P2-G8** — DEFERRED out of Phase 2: whole-doc `[:2000]` de-dup (common-path embedding change; its own measured A/B later).
- [ ] **P2-G9** — PDF ingestion does NOT regress the common path (eval with PDFs added, deep OFF then ON).
- [ ] **Deferred (loud):** G3 section-structure, G7 office-dilution, gate wiring/cutover → Track C/Phase 3.

---

## 11. Reject criteria (revised, finding #6 — split, per-question)
1. **Per-question common-case no-regression (BINDING):** with deep-fallback ON, **no previously-correct common-case answer becomes incorrect** (owner may approve an explicit tolerated delta; default = zero). Aggregate ≥84 within judge σ is **secondary, not sufficient**.
2. **Deep set improves materially** (the M2 win is real, beyond judge σ).
3. **PDF ingestion alone (deep OFF) does not regress the common case** (P2-G9).
4. **PDF text faithful + mechanical-only:** text-preserving normalization only; image/invalid → skip-flagged; degraded tables served with the source-link + warning safeguard, never as clean authoritative figures.
5. **Invalidation holds:** invariant passes on a copy after a simulated recrawl + a simulated model-id change (no orphan/stale/wrong-model chunks).
6. **Flag-off control flow unchanged; corpus changes tested separately; every change gated + reversible; immortal posts/judging untouched.**

---

## 12. Build sequencing (for writing-plans)
1. **B — `pdf_extract.py`** (pure, fixture-tested; no retrieval change).
2. **A — `_retrieve(semantic_mode)` refactor + `retrieve_deep`** (parity tests; `retrieve()` behavior unchanged).
3. **A — miss-ladder wiring** behind `RETRIEVAL_DEEP_FALLBACK` (OFF): adopt-if-better, None≠miss, office→deep→live, query_vec reuse, diversity refuse-adopt.
4. **A — calibrate `DEEP_FALLBACK_THRESHOLD` + full-pipeline eval.sh A/B** (per-question no-regression — the binding gate).
5. **C — invalidation completeness** (GC + reconcile + invariant + build-version gating) + writer audit.
6. **B — PDF ingestion wiring** (`type='pdf'`, recrawl, degraded safeguards) + requirements; **eval PDFs-added deep-OFF (P2-G9) then deep-ON**.
7. **C — re-chunk/embed pass** on a copy; live embed at the owner checkpoint.

Each retrieval/answer-touching step: senior-eng + RAG review (HARD GATE), owner approval, TDD.

---

## 13. Resolved questions (from rev-1 open list + review)
1. **retrieve_deep vs RETRIEVAL_CHUNKS:** dedicated `retrieve_deep` over a shared `_retrieve(semantic_mode)` core (#4). Resolved.
2. **Threshold source:** distinct, calibrated by false-adoption cost; NOT inherited from LIVE_THRESHOLD (#5). Resolved.
3. **Grid/table PDFs:** not deferrable; degraded-table serving safeguard now (#7/#18). Resolved.
4. **Live re-embed timing:** fold into the DB wipe+rebuild (free re-embed); standalone-gated only if Phase 3 needs it sooner. (Owner checkpoint.)
5. **Whole-doc de-dup:** DEFERRED out of Phase 2 (#13) — not behavior-neutral.
6. **relevance=None:** conservative — NOT a miss; no deep rescue (#3).

---

## 14. Out of scope (explicit)
G3 section-structure; G7 office-dilution routing; the answer-gate build/cutover; the curation cycle; C golden tier; `catalog.njit.edu`/E crawler seeds (Phase 1's domain); whole-doc `[:2000]` de-dup. Phase 2 ends when A+B+C ship and are proven on a copy (per-question no-regression + deep-recall win + PDF faithfulness + invariant), with the live re-embed staged for the Phase-3 corpus build.
