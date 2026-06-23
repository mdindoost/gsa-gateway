# High-Stakes Office Content — Cleanup + Hybrid Verbatim Serve — Design

**Date:** 2026-06-22
**Status:** DESIGN — brainstormed + owner-confirmed; **two opus expert reviews done (senior-eng + RAG/anti-fab); all blockers + should-fixes folded in** (see Expert-review record). Pending owner review → writing-plans → HARD GATE build.
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
Change-detection skips unchanged pages (same content hash), so a classifier change does NOT re-apply to existing rows. A gated migration sets, **per page** (keyed on `source_url` — one page = many chunks sharing `source_url`/`doc_id` [SE-P1]):
- **False positive → `is_active=1`, drop `metadata.stakes`** → served by the normal office tier (compose).
- **Genuine high-stakes → `is_active=1`, keep `metadata.stakes='high'`** → served by the Part-2 hybrid path (never composed).

**Source of truth = the human-confirmed 51-page split (1c), NOT a re-derivation** [SE-P2/P1]. The refined `is_high_stakes` only *generates* the candidate list for review; the migration reads the **confirmed `source_url → stakes` decision list** and applies it (the original whole-page text isn't stored intact, so re-running the text rule on reassembled chunks is fragile — avoid it). `UPDATE … WHERE source_url=? AND type='office_page'` flips `is_active` + edits `metadata.stakes` for all chunks of the page atomically.

**Do NOT touch `office_page_state`** [SE-P4]: `is_active`/`stakes` live on `knowledge_items`; the change-detection hash stays on `office_page_state`, so the next crawl still sees an unchanged hash → skips re-ingest (`leg='unchanged'`) → the activation persists. Idempotent (re-running is a no-op UPDATE).

After this **nothing is invisible**: generic → fast path, high-stakes → safe path; `stakes='high'` is the only router. Gated: `hardened_backup`, dry-run default, `--commit`. **Then re-embed** (`embed_all.py`) — the newly-activated generic chunks need vectors for the office tier's semantic leg; verify whether `embed_all` had already embedded `is_active=0` rows (if it skips inactive rows, this embed is load-bearing) [SE-P3].

### 1c. Human-in-the-loop review (before applying)
Produce the **genuine-vs-false-positive classification of all 51 staged pages** (URL + my call + reason) for the owner to review/adjust — the owner has final say on "high-stakes." The migration reads that confirmed split (or the refined classifier, reconciled with the review).

### 1d. Dilution check
Activated high-stakes pages enter the office-tier retrieve but remain `office_page` (in `DEFAULT_EXCLUDE_TYPES`, Plan B) → **never in the curated corpus**. `stakes='high'` guarantees the verbatim path, never a paraphrase.

---

## Part 2 — Hybrid verbatim serve (ANSWER PATH; full hard gate) — REVISED per expert review [SE/RA blockers]

### 2a. Trigger + early-return (`bot/core/message_handler.py::_rag_pipeline`) [SE-B7]
Inside the existing office-tier block, after office chunks are adopted (`used_office=True`): if the **top adopted chunk is `stakes='high'`** AND its relevance clears **`HIGH_STAKES_THRESHOLD`** (a NEW floor, `> OFFICE_THRESHOLD`, default e.g. 0.30 — higher than generic office so a marginally-related high-stakes page can't hijack the verbatim path [RA-S2]), build the high-stakes `MessageResponse` and **`return` it directly out of `_rag_pipeline`** — mirroring the `used_live` short-circuit. It handles its own heads-up + `db.log_question` + `question_id`, so it must NOT fall into the compose block, the `apply_headsup` at the tail, or the `offer_live_search`/`attempted_live` logic (no double-compose / double-heads-up) [SE-B7, SE-B8].

### 2b. `_answer_high_stakes(req, question, chunk) -> MessageResponse`
Reuses the EXISTING verbatim machinery but in the bot's ASYNC shape (it canNOT call `grounded_extract.answer_from_page`, which calls `call_llm` synchronously — the bot's Ollama is async) [SE-B2]:
1. **Fetch** the chunk's exact URL with the robots/SSRF-aware fetcher, built ONCE (per-handler, not per request) [SE-B6]: `html, status = self._office_fetch(chunk.source_url)`. **Success = `html is not None`** (status is an int HTTP code — NEVER compare to the string `"ok"`) [SE-B6/RA-B1].
2. **Fresh path** (`html` present): `text_page = clean_html(html)` (import `web_crawler.clean_text` **as `clean_html`** — `clean_text` is a local string in `_rag_pipeline`) [SE-B1]; `sys_p, usr_p = grounded_extract.build_extract_prompt(question, text_page)`; `raw = await self.ollama.generate(usr_p, sys_p)`; `ans = grounded_extract.ground_spans(raw, text_page, chunk.source_url)`. If `ans` (≥1 span survived literal-grounding): `body = _format_spans(ans.spans)` (render `ans.spans: list[str]` the way `live_fallback._format` does — NOT `.text`, which doesn't exist) [SE-B3]; `is_live=True`.
3. **Offline fallback** (no `html`, OR `ans is None`): **conservative branch** [RA-S1]:
   - If the page is in the **volatile subset** (`source_url`/content matches `tuition|deadline|important-dates|payment|refund|\$`): **decline the exact figure** — `body = "I can't verify today's figure/deadline — please check the live page."` (do NOT render the possibly-stale number).
   - Else (non-volatile high-stakes prose): `body = chunk.content` (stored verbatim — field is `content`, not `text`) [SE-B3] prefixed with **"ℹ️ As of `<crawl_date>`:"**.
   - `is_live=False`.
4. **Unconditional heads-up** [SE-B5/RA-B2]: append a confirm-with-the-office line **independent of `apply_headsup`/`match_topic`** (which only covers immigration/billing/funding and would MISS parking-fees/health-insurance/residency): `body += f"\n\n⚠️ Confirm on the live page — figures, deadlines and rules change: {chunk.source_url}"`. (Keeping it source-link-based makes the guarantee structural, not pattern-dependent.)
5. Log the question (`db.log_question(..., matched_topic="office high-stakes")`) and return `MessageResponse(text=body, source_note=chunk.source_url, used_ai=True, is_live=is_live, question_id=qid, offer_live_search=False)`. **`ollama.generate_answer` (the paraphrasing compose) is NEVER called.**

**Refactor to avoid duplication:** factor the fetch→clean→prompt→await-generate→ground_spans sequence out of `live_fallback.maybe_answer_live` into a shared `extract_one_url(url, question, fetch, generate)` helper; both the Brave path and `_answer_high_stakes` call it (the Brave path supplies the searched URL; the high-stakes path supplies `chunk.source_url`) [SE-B2].

### 2c. Plumbing (bigger than first stated) [SE-B3/B4, RA-plumbing]
- `RetrievedChunk` (`retriever.py:80`, has `content`/`source_url`/`verified` — NOT `text`, `stakes`, or a date) gains **`stakes`** (from `metadata.stakes`) and **`crawl_date`**. The hydration SELECT (`retriever.py:339`) currently selects `id,title,type,content,org_id,metadata,source_url` with **no timestamp** → add **`updated_at`**; set `crawl_date = updated_at`.
- The **`V1Chunk` shim** (`retriever_shim.py`) ALSO drops these — add `stakes` + `crawl_date` there too (the two-hop hydration the bot actually uses).
- **`crawl_date` = `knowledge_items.updated_at`** (the content-write time), NOT `office_page_state.last_seen_at` (which is bumped on every crawl even when content is unchanged → would claim a freshness the stored snapshot doesn't have) [SE-B4/RA-S4].

### 2d. Precedence (unchanged ladder, refined branch)
`structured → curated RAG → office tier { generic → compose | high-stakes (≥HIGH_STAKES_THRESHOLD) → hybrid-verify } → live Brave → deflection`. Hybrid-verify preempts the Brave-search fallback — more precise (known URL), cheaper (no search), fresher. **PARTIAL-GUARANTEE FLAG [RA-S3]:** because the office tier fires only on a *primary miss*, a high-stakes question that the curated corpus *shadows* (a weak curated chunk ≥ `LIVE_THRESHOLD`) never reaches this path and is answered by the **composed/paraphrased** curated chunk — i.e. the no-paraphrase guarantee covers ONLY high-stakes questions that reach the office tier, NOT all of them. Full coverage requires the deferred office-tier shadowing fix. This is loudly flagged, not solved here.

---

## Part 3 — Freshness, flags, error handling

- **Freshness display:** fresh leg = the live figure (heads-up covers volatility); offline leg = either **"As of `<YYYY-MM-DD>`"** (non-volatile prose) or an explicit **decline + live-page pointer** (volatile `$`/deadline subset, [RA-S1]).
- **Response flags:** fresh → `is_live=True`; offline → `is_live=False`; `used_ai=True` both; `offer_live_search=False` (returned before that logic) [SE-B7].
- **Error handling:** `_office_fetch` degrades to `(None, …)` on transport error / robots block / non-HTML → offline. `ground_spans` returns `None` if no span is literally on the page → offline (never fabricate). Malformed page → `clean_html` returns `""` (the 2026-06-22 hardening) → offline. 20s fetch timeout (`web_crawler.TIMEOUT`) → offline on a slow page.
- **Cost:** ~1 direct GET + 1 extract LLM call, only on high-stakes turns — cheaper than today's Brave-search live fallback (skips the search).

## Anti-fabrication guarantees (the heart of this)
- High-stakes content is **never** sent to `generate_answer` (the paraphrasing path). Fresh = `ground_spans` verbatim spans (literal-on-page or dropped). Offline = the stored verbatim chunk **or a decline-to-the-live-page** for the volatile `$`/deadline subset [RA-S1].
- Source link + a confirm-with-the-office heads-up on **every** high-stakes answer — made **unconditional** (not `apply_headsup`-pattern-dependent) so parking-fees/health-insurance/residency are covered [SE-B5/RA-B2].
- `office_page` stays excluded from the curated corpus (no dilution).
- **PARTIAL GUARANTEE (loud):** the no-paraphrase guarantee covers only high-stakes questions that **reach the office tier**. A high-stakes question *shadowed* by a weak curated chunk (≥ `LIVE_THRESHOLD`) is still answered by the **composed** curated chunk. Full coverage needs the deferred office-tier shadowing fix [RA-S3].

## Testing
**The unit tests are the PRIMARY anti-fabrication gate** (`eval.sh`'s local judge won't reliably catch a subtly-paraphrased figure); `eval.sh` is the *regression* gate [RA-S5].
- **Part 1:** unit-test the refined `is_high_stakes` against **all 51 staged URLs** (the false positives → not high-stakes incl. `daily-parking-options`/`amazon-lockers`/`faqs`; the genuine → high-stakes incl. `employee-parking-fees`; substring guards hold) [SE-P5]. Migration test on a temp DB driven by a **confirmed split list**: false-positive page → all its chunks `is_active=1`, no `stakes`; high-stakes page → `is_active=1`, `stakes='high'`; `office_page_state` untouched; idempotent.
- **Part 2** (injected `fetch` + injected async `generate` — no network/Ollama):
  - (a) fetch ok + a literal span → fresh answer body is a **literal substring** of the page, `is_live=True`, **`ollama.generate_answer` asserted never called**;
  - (b) fetch fails → offline: non-volatile page → stored `content` + "As of <date>"; **volatile (`$`/deadline) page → decline + live link (no number)** [RA-S1]; `is_live=False`;
  - (c) fetch ok but no literal span → offline (no fabrication);
  - (d) **routing:** a `stakes='high'` chunk above `HIGH_STAKES_THRESHOLD` → `_answer_high_stakes`; a generic chunk → compose; a high-stakes chunk *below* the floor → not adopted [RA-S2];
  - (e) **early-return flags:** the high-stakes path sets `offer_live_search=False`, correct `is_live`, and the heads-up appears exactly once (no double) [SE-B7];
  - (f) **unconditional heads-up:** a parking-fees chunk (no `apply_headsup` topic) still gets the confirm-with-source line [SE-B5].
- **Curated-regression gate:** `bash scripts/eval.sh` on the existing set must not drop. Add the high-stakes verification Qs to `eval/questions.txt`.
- **Chat verification (dev/live):** "how much is a parking permit", "bursar payment options", "tuition remission for employees", "OPT application steps", "J-1 student requirements", "important bursar dates" → fresh verbatim + source + heads-up; a forced-offline volatile case → decline + live link; a forced-offline prose case → stored + "as of <date>".

## Decisions (owner-confirmed in brainstorming)
- **D1 — goal:** ✅ BOTH (cleanup + verbatim-serve + activate).
- **D2 — serve source for genuine high-stakes:** ✅ **HYBRID** (KB routes → live-fetch fresh → grounded-extract; stored verbatim as offline fallback + "as of <date>").
- **D3 — architecture:** ✅ stored content = precise router + offline net; live fetch = freshness; `stakes='high'` is the router.
- **D4 — cleanup:** ✅ refine classifier + gated re-classification migration + human review of the 51 pages.

## Decomposition (two plans, sequenced)
- **Plan 1 — Cleanup + activation (data):** 1a refine classifier, 1b migration, 1c review, re-embed. Independently shippable: generic pages go live, high-stakes activated + tagged. No answer-path change → no restart.
- **Plan 2 — Hybrid serve (answer path):** 2a–2d + plumbing. Builds on the `stakes='high'` marker. Full hard gate (RAG + senior review, curated-regression eval, owner sign-off, merge + restart).

## Goals checklist (verify at build)
- [ ] `is_high_stakes` tightened; unit-tested against all 51 URLs (keep `employee-parking-fees`, drop `daily-parking-options`/`amazon-lockers`/`faqs`) — SHIP (Plan 1)
- [ ] 51-page human-review list produced + owner-confirmed = the migration's source of truth — SHIP (Plan 1)
- [ ] Gated re-classification migration (per-`source_url`; false-positives→live-generic, genuine→active+`stakes='high'`; leaves `office_page_state` untouched; idempotent); re-embed — SHIP (Plan 1)
- [ ] `RetrievedChunk` **and** `V1Chunk` shim expose `stakes` + `crawl_date`; hydration SELECT adds `updated_at`; `crawl_date=updated_at` (not `last_seen_at`) — SHIP (Plan 2) [SE-B3/B4]
- [ ] Shared `extract_one_url` helper (async: fetch→`clean_html`→`build_extract_prompt`→`await generate`→`ground_spans`); `live_fallback` refactored onto it — SHIP (Plan 2) [SE-B2]
- [ ] `_answer_high_stakes`: fresh verbatim spans / offline (verbatim+as-of-date OR decline-for-volatile); **unconditional** confirm-with-source heads-up; **never** composes; clean `MessageResponse` returned out of the ladder (early-return, no double) — SHIP (Plan 2) [SE-B1/B5/B7, RA-S1/B2]
- [ ] `HIGH_STAKES_THRESHOLD` (> OFFICE_THRESHOLD) floor; routing/flags correct — SHIP (Plan 2) [RA-S2]
- [ ] **Unit tests are the PRIMARY anti-fab gate** (never-composed, verbatim-substring, decline-on-volatile-offline) + `eval.sh` regression gate green — SHIP (Plan 2) [RA-S5]
- [ ] **LOUDLY FLAGGED partial guarantee:** no-paraphrase covers only office-tier-reached questions; shadowed high-stakes Qs still composed until the shadowing fix — DOC (Plan 2) [RA-S3]
- [ ] (DEFER, flagged) office-tier *shadowing* fix (separate design); Wave-2 offices

## Expert-review record (2026-06-22) — both opus reviews, all fixes folded above
- **Senior-eng** (PLAN-WITH-BLOCKERS): B1 `clean_text` local-var shadow → alias `clean_html`; B2 async/sync — can't use `answer_from_page` (sync `call_llm`), replicate `maybe_answer_live`'s `await generate` shape via a shared `extract_one_url`; B3 field names `chunk.content`/`ans.spans` (not `.text`); B4 add `updated_at` to the SELECT, use it (not `last_seen_at`); B5 heads-up gap → unconditional line; B6 `fetch_with_status` int status (not "ok") + build the fetcher once; B7 explicit early-return (no double-compose/heads-up); B8 log the question; P1/P2 migration keyed on `source_url`, confirmed-list is source of truth; P3 verify embed active-filter; P4 don't touch `office_page_state`; P5 keep `employee-parking-fees`.
- **RAG/anti-fab** (no-paraphrase HOLDS; honest-partial had gaps): B1 int status; B2 unconditional heads-up; S1 offline leg declines stale `$`/deadline figures; S2 `HIGH_STAKES_THRESHOLD` + deflect-on-no-spans; S3 flag shadowing partial-guarantee loudly; S4 `updated_at` not `last_seen_at`; S5 unit tests are the primary anti-fab gate. Verdict: buildable once 2b/2c rewritten against the real async/field reality (done above).
