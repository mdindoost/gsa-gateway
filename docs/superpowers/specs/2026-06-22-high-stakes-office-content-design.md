# High-Stakes Office Content — Cleanup + Hybrid Verbatim Serve — Design

**Date:** 2026-06-22
**Status:** DESIGN (brainstormed + owner-confirmed section-by-section; pending spec review → writing-plans → HARD GATE build)
**Author:** Mohammad Dindoost (owner) + Claude (design)
**Builds on:** the NJIT prose-harvest system (live, `main` ≥ `666080e`) — `office_page` tier, `is_high_stakes` staging, the live njit.edu fallback (`grounded_extract`), `fetch_with_status`, `office_page_state` (crawl dates).
**Related:** [[project_full_njit_mirror]], `docs/superpowers/specs/2026-06-22-njit-prose-harvest-design.md` (§4.3 [RA4]).

## Problem

The prose harvest stages high-stakes office pages (`is_active=0`, `stakes='high'`) so the LLM can't paraphrase money/immigration/deadline facts — but that left **376 staged chunks / 51 pages invisible to retrieval**, served (if at all) only by the live Brave fallback. Two issues:
1. **Over-staging** — the broad `is_high_stakes` classifier caught genuinely-generic pages (`bursar/faqs`, `registrar/frequently-asked-questions`, `parking/daily-parking-options`, `mailroom/amazon-lockers-njit`, `parking/event-game-day-parking`) alongside the real ones. Those generic pages are needlessly withheld.
2. **No safe serve path** — for the genuinely high-stakes pages (`bursar/payment-options`, `bursar/employee-tuition-remission`, `bursar/important-dates`, `global/health-insurance-information-international-students`, `global/incoming/j1-students-exchange`, `parking/employee-parking-fees`, `registrar/how-residency-determined`), we want our KB to answer — but **without paraphrasing**, and **fresh** (a stale tuition figure is the worst failure).

## Goals

1. **Cleanup:** stop withholding generic pages — route them to the normal (fast, composed) office tier.
2. **Serve genuine high-stakes from our KB, safely:** verbatim (never paraphrased) **and** current.
3. **Resolve the freshness tension:** high-stakes facts must be as fresh as possible, not a stale snapshot.
4. **No dilution / honest-partial preserved.** No fabrication. Full hard gate on the answer-path change.

**Non-goals:** the office-tier *shadowing* fix (separate follow-up); high-stakes pages that the curated corpus already shadows (handled by that follow-up); Wave-2 office expansion.

## Core idea (owner-confirmed)

A stored high-stakes page already knows its **exact `source_url`**, so the stored content becomes a **precise router into a fresh, grounded answer** rather than a stale snapshot served directly:

> high-stakes question → office tier surfaces the stored high-stakes chunk (we now know the exact page) → **live-fetch that one page** (direct GET, no Brave search) → `grounded_extract` the current figure as **verbatim spans** → serve + source link + heads-up. On live-fetch failure → serve the **stored verbatim** copy + **"as of `<crawl date>`"** + heads-up.

**Stored = the precise router + the offline safety net; the live fetch = freshness.** Cheaper than today's live fallback (skips the Brave search) and fresher than the snapshot.

`stakes='high'` is the router: generic office chunks → normal compose; high-stakes chunks → this hybrid-verify path. Erring toward staging stays safe (a mis-staged generic page just gets a correct, fresh live-fetch; only *missing* a real high-stakes page — serving it composed/stale — is dangerous).

---

## Part 1 — Cleanup + activation (DATA; no answer-path change)

### 1a. Tighten `is_high_stakes` (`v2/core/ingestion/office_ingest.py`)
Narrow the URL rule so it no longer catches generic pages. KEEP (stage): `opt|cpt|i-?20|i-?765|sevis|visas?|tuition|billing|payment|refund|deadlines?|important-dates`. DROP from the URL rule (these caused the false positives): the bare `fees?` token's over-reach and FAQ/options/lockers/event slugs — i.e. require a *procedure/figure* slug, not a topic landing page. KEEP the `$<digit>` + payment-intent **text rule** unchanged (it's the real safety net for a `$`-amount on any page). Exact term list finalized against the 51-page review (1c).

### 1b. One-time gated re-classification migration (`scripts/_reclassify_office_stakes.py`)
Change-detection skips unchanged pages (same content hash), so a classifier change does NOT re-apply to existing rows. A small gated migration re-runs the refined classifier over existing `office_page` rows and sets, per page:
- **False positive → `is_active=1`, drop `stakes`** (metadata `stakes` removed) → served by the normal office tier (compose).
- **Genuine high-stakes → `is_active=1`, keep `stakes='high'`** → served by the Part-2 hybrid path (never composed).

After this **nothing is invisible**: generic → fast path, high-stakes → safe path; `stakes='high'` is the only router. Gated: `hardened_backup`, dry-run default, `--commit`; re-embed the newly-activated generic chunks (`embed_all.py`) after.

### 1c. Human-in-the-loop review (before applying)
Produce the **genuine-vs-false-positive classification of all 51 staged pages** (URL + my call + reason) for the owner to review/adjust — the owner has final say on "high-stakes." The migration reads that confirmed split (or the refined classifier, reconciled with the review).

### 1d. Dilution check
Activated high-stakes pages enter the office-tier retrieve but remain `office_page` (in `DEFAULT_EXCLUDE_TYPES`, Plan B) → **never in the curated corpus**. `stakes='high'` guarantees the verbatim path, never a paraphrase.

---

## Part 2 — Hybrid verbatim serve (ANSWER PATH; full hard gate)

### 2a. Trigger (`bot/core/message_handler.py::_rag_pipeline`)
Inside the existing office-tier block, after office chunks are adopted (`used_office=True`): if the **top adopted chunk is `stakes='high'`**, route to `_answer_high_stakes(...)` instead of the compose/generate block.

### 2b. `_answer_high_stakes(req, question, chunk) -> MessageResponse`
1. `html, status = fetch_with_status()(chunk.source_url)` — direct GET, robots-aware, no Brave search.
2. **Fresh path** (html present): `spans = grounded_extract.answer_from_page(question, clean_text(html), chunk.source_url, call_llm)`; if `spans`, `text = spans.text` (the verbatim rendered spans, same field the live fallback uses as `live.text`), `source_note = chunk.source_url`, `is_live = True`, `used_ai = True`.
3. **Offline fallback** (no html OR no spans): `text = chunk.text` (stored verbatim), prepend/append **"ℹ️ as of `<crawl_date>`"** (from `office_page_state.last_seen_at` / the chunk's `updated_at`), `source_note = chunk.source_url`, `is_live = False`.
4. **Always** `text = apply_headsup(text, question)` so the office/billing/immigration heads-up is appended; ensure the relevant heads-up topic matches (operations/billing/immigration).
5. Return `MessageResponse(text=text, source_note=source_note, used_ai=True, is_live=is_live, question_id=…)`. `ollama.generate_answer` is **never** called on this path.

### 2c. Plumbing (small)
`RetrievedChunk` exposes **`stakes`** (from `metadata.stakes`) and a **`crawl_date`** (the row's `updated_at` or the `office_page_state.last_seen_at` for `source_url`). The retriever already reads `metadata` + timestamps — add the two fields. `call_llm` is wired exactly as the live fallback wires it for `grounded_extract`.

### 2d. Precedence (unchanged ladder, refined branch)
`structured → curated RAG → office tier { generic → compose | high-stakes → hybrid-verify } → live Brave → deflection`. The hybrid-verify preempts the Brave-search fallback for these topics — more precise (known URL), cheaper (no search), fresher.

---

## Part 3 — Freshness, flags, error handling

- **Freshness display:** fresh path = the live figure (no date caveat needed beyond the heads-up); offline fallback = explicit **"as of `<YYYY-MM-DD>`"** so the user knows it's a snapshot.
- **Response flags:** fresh → `is_live=True` (it IS a live njit.edu answer); offline → `is_live=False`. `used_ai=True` both ways. The high-stakes path answered, so the deflection-offer logic does not fire (not a deflection).
- **Error handling:** `fetch_with_status` already degrades (None on transport error / robots block) → offline fallback. `grounded_extract` returns None if no span appears literally → offline fallback (never fabricate). A malformed page → `clean_text` returns "" (the 2026-06-22 hardening) → offline fallback.
- **Cost:** ~1 direct GET + 1 extract LLM call, only on high-stakes questions (a small slice). Cheaper than today's Brave-search live fallback.

## Anti-fabrication guarantees (the heart of this)
- High-stakes content is **never** sent to `generate_answer` (the paraphrasing path). Fresh = `grounded_extract` verbatim spans (literal-on-page or dropped). Offline = the stored verbatim chunk.
- Source link on every high-stakes answer; heads-up on every high-stakes answer.
- `office_page` stays excluded from the curated corpus (no dilution).

## Testing
- **Part 1:** unit-test the refined `is_high_stakes` against the 51-page set (false positives → not high-stakes; genuine → high-stakes; the `coffee`/substring guards hold). Migration test on a temp DB: a false-positive row → `is_active=1`, no `stakes`; a high-stakes row → `is_active=1`, `stakes='high'`; idempotent.
- **Part 2:** `_answer_high_stakes` with an **injected fetch + injected call_llm** (no network/Ollama): (a) fetch OK + spans → fresh verbatim answer, `is_live=True`, **`generate_answer` never called**; (b) fetch fails → stored verbatim + "as of <date>", `is_live=False`; (c) fetch OK but no literal span → offline fallback (no fabrication). Plumbing test: a `stakes='high'` chunk routes to `_answer_high_stakes`, a generic chunk routes to compose.
- **Curated-regression gate:** `bash scripts/eval.sh` on the existing question set must not drop (the office-tier/high-stakes branch must not regress curated/GSA answers). This is a release gate, not a footnote.
- **Chat verification (dev/live):** "how much is a parking permit", "what are the bursar payment options", "tuition remission for employees", "OPT application steps", "J-1 student requirements", "important bursar dates" → fresh verbatim + source + heads-up; and a forced offline case (fetch disabled) → stored + "as of <date>".

## Decisions (owner-confirmed in brainstorming)
- **D1 — goal:** ✅ BOTH (cleanup + verbatim-serve + activate).
- **D2 — serve source for genuine high-stakes:** ✅ **HYBRID** (KB routes → live-fetch fresh → grounded-extract; stored verbatim as offline fallback + "as of <date>").
- **D3 — architecture:** ✅ stored content = precise router + offline net; live fetch = freshness; `stakes='high'` is the router.
- **D4 — cleanup:** ✅ refine classifier + gated re-classification migration + human review of the 51 pages.

## Decomposition (two plans, sequenced)
- **Plan 1 — Cleanup + activation (data):** 1a refine classifier, 1b migration, 1c review, re-embed. Independently shippable: generic pages go live, high-stakes activated + tagged. No answer-path change → no restart.
- **Plan 2 — Hybrid serve (answer path):** 2a–2d + plumbing. Builds on the `stakes='high'` marker. Full hard gate (RAG + senior review, curated-regression eval, owner sign-off, merge + restart).

## Goals checklist (verify at build)
- [ ] `is_high_stakes` tightened; verified against the 51-page review — SHIP (Plan 1)
- [ ] Gated re-classification migration; false-positives→live-generic, genuine→active+`stakes='high'`; re-embed — SHIP (Plan 1)
- [ ] 51-page human-review list produced + owner-confirmed — SHIP (Plan 1)
- [ ] `RetrievedChunk` exposes `stakes` + `crawl_date` — SHIP (Plan 2)
- [ ] `_answer_high_stakes` hybrid path (fresh grounded-extract / offline verbatim + as-of-date), heads-up, never composes — SHIP (Plan 2)
- [ ] Precedence branch wired (high-stakes preempts Brave); response flags correct — SHIP (Plan 2)
- [ ] Curated-regression eval gate green; chat verifications pass — SHIP (Plan 2)
- [ ] (DEFER, flagged) office-tier *shadowing* fix (separate design); Wave-2 offices
