# KB-First + Live njit.edu Search Fallback (Grounded Extractive) — Design

**Date:** 2026-06-17
**Status:** Approved (design); pending senior-eng review before build
**Relates to:** `project_day_to_day_intents` (covers the long tail without pre-building every
intent), `2026-06-17-page-crawler-design.md` (Sub-project 2 — the batch crawler that reuses this
spec's grounded-extract core), the immigration/billing/funding **heads-up** (already live),
and the shelved answerability-gate idea (now upgraded: a KB miss → *go find it* instead of decline).

## Goal & shape

Answer NJIT questions the KB doesn't confidently cover by **live-searching njit.edu, fetching the
top page, and answering from verbatim, page-grounded excerpts + the source link** — never inventing.
This is **Sub-project 1** of the chosen architecture (KB-first + live fallback); the batch crawler
(Sub-project 2) reuses the same grounded-extract core to pre-populate the KB later.

**Senior-review-driven decision: extractive, not generative.** The doc/answer body is **verbatim
spans the LLM selected and we verified are literally on the page** — no generative rewrite. This
eliminates the combination/paraphrase hallucination class the review flagged (e.g. attaching a CPT
rule to OPT, "90 days before" vs "after"). The LLM's only job is *selection*; correctness is
enforced by literal-presence grounding of every span we keep.

## Architecture & data flow

```
question
  → KB retrieve (existing hybrid + rerank)
  → answerable from KB?  (top reranked chunk's relevance ≥ threshold)
       yes → answer from KB (today's path) + heads-up
       no  → LIVE FALLBACK:
              search njit.edu (Google Programmable Search)  → top URLs
              → http_fetch top 1–2 pages (raw_pages cache)  [reuse]
              → answer_from_page(question, page_text)        [grounded extractive]
                   = LLM selects verbatim spans answering the question;
                     keep only spans literally present on the page; assemble
              → grounded answer found? → reply with spans + source link + heads-up
                   else → decline + route to the relevant office (today's deflection)
```

## Components

### 1. `v2/core/ingestion/grounded_extract.py` (the shared core)
`answer_from_page(question, page_text, source_url, call_llm) -> AnswerSpans | None`.
- The LLM is asked to return the **verbatim sentences/spans from the page that answer the
  question** (JSON list of quotes), nothing rewritten.
- We **keep a span only if it appears literally on the page** (substring after whitespace
  normalization) — the trust core; a paraphrase/hallucination is dropped.
- If no span survives → return `None` (the page doesn't answer; we will NOT fabricate).
- Pure parsing/grounding is unit-tested; `call_llm` is injected (offline tests).
- This same function backs Sub-project 2's batch crawler (extract per page → doc).

### 2. `v2/integration/njit_search.py` (search client)
`search(query, k=3) -> list[str]` over the **Google Programmable Search JSON API**, restricted to
njit.edu via the configured CSE. Reads `GOOGLE_CSE_ID` / `GOOGLE_API_KEY` from env (in `.env`).
Network call is **injected/mockable** (`search(query, http_get=...)`), so unit tests need no key.
Returns `[]` on any error (missing key, quota, network) → fallback degrades to today's decline.

### 3. Live-fallback orchestration (`bot/core/live_fallback.py`, called from `message_handler`)
`async maybe_answer_live(question) -> LiveAnswer | None`:
- search njit.edu → for the top 1–2 results, `http_fetch` (reuse `explore.http_fetch`; respects
  robots/UA) → `answer_from_page`. First grounded answer wins.
- Returns the answer text (the verbatim spans, lightly formatted) + the **source URL** (cited).
- Returns `None` if search/fetch/extract yields nothing → caller keeps today's "contact the office".
- Wired into the RAG branch of `message_handler`: replaces the bare "I couldn't find it" deflection
  with "try live, else deflect." The immigration/billing/funding heads-up still appends.

### The KB-miss trigger
KB is "answerable" when the top reranked chunk clears a relevance threshold. We surface the
cross-encoder relevance of the top chunk (a small addition to the retriever path — the
`rerank_score` plumbing the earlier answerability spec described, now actually used) and treat
below-threshold (or zero-chunks) as a miss → live fallback. Threshold is calibrated on a labeled
covered/uncovered set (admissions/officers = covered; parking/wifi/long-tail = uncovered→live).
Admin-tunable; `0` disables the live path (kill-switch).

## Safety (the whole point)

- **Extractive only** — the answer is verbatim page text; no generative combination → the
  review's hallucination class is gone.
- **Literal-presence grounding** of every span kept; non-present spans dropped; no spans → no
  answer (decline, don't fabricate).
- **Source link cited** on every live answer (traceable), plus the immigration/billing/funding
  heads-up.
- **Search restricted to njit.edu** (the CSE config) → we only read authoritative pages.
- Live path is **opt-in / kill-switchable** and degrades safely to today's behavior if the key is
  absent or anything errors.

## Latency & cost

Live path runs **only on a KB miss**: ~2–5 s (search + 1–2 fetches + one LLM call). Covered
questions are unaffected (instant KB). Google PSE free tier ≈ 100 queries/day; cache the
question→answer briefly to avoid repeats (raw_pages already caches fetched pages).

## Error handling

| Condition | Behavior |
|---|---|
| No key / search error / quota | `search` returns `[]` → live path no-ops → today's decline |
| Fetch fails / robots-disallow / non-HTML | that result skipped; try next; none → decline |
| LLM returns junk / no grounded span | return `None` → decline (never fabricate) |
| `live_enabled=0` | live path skipped entirely (kill-switch) |

## Testing & acceptance

**Unit (offline, injected search + injected LLM):**
- `answer_from_page`: keeps only spans literally on the page; **drops a hallucinated span** (the
  safety property); returns `None` when no span answers.
- `njit_search`: parses CSE JSON to URLs; returns `[]` on error; never raises.
- orchestration: KB-miss → search→fetch→extract path (all mocked) → grounded answer + link;
  page-can't-answer → `None` → caller deflects.

**Acceptance gate (deterministic where possible):**
- A labeled **uncovered set** (questions not in the KB, e.g. parking, spring-break, a niche
  admissions detail) → the mocked-source pipeline returns a grounded answer citing the (mocked)
  njit.edu URL; the **safety test** (hallucinated span dropped) passes.
- **Live smoke (needs the key, run once):** a few real uncovered questions → a real njit.edu link +
  a verbatim-grounded answer; a clearly-unanswerable query → graceful decline.
- **0 regressions:** covered questions still answer from the KB at the same latency (trigger only
  fires below threshold).

**Acceptance bar:** uncovered questions get grounded, source-cited live answers; hallucinated spans
are dropped; covered questions unchanged; kill-switch + no-key both degrade to today's behavior.

## Out of scope
- The batch crawler (Sub-project 2 — reuses `grounded_extract`).
- Multi-page synthesis / following links from the search result (top page only for now).
- Caching beyond the existing `raw_pages` + a short question→answer memo.

## Files
- Create `v2/core/ingestion/grounded_extract.py`, `v2/integration/njit_search.py`,
  `bot/core/live_fallback.py`.
- Modify `bot/core/message_handler.py` (RAG-branch miss → live fallback), `bot/config.py`
  (`GOOGLE_CSE_ID`, `GOOGLE_API_KEY`, `live_enabled`/threshold), the retriever to surface the top
  rerank relevance.
- Create `v2/tests/test_grounded_extract.py`, `v2/tests/test_njit_search.py`,
  `v2/tests/test_live_fallback.py`.
