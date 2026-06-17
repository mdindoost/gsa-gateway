# Answerability Gate + Router Precision — Design

**Date:** 2026-06-17
**Status:** Approved + senior-eng review incorporated — C1 signal→margin (data-decided, not
absolute sigmoid), C2 impeach=answerable, S1 best-real-score across pool, S2/S3 per-call
threshold read (instant kill-switch, no getattr), S4 gate at shared generation point (covers
social+retry), S5 ≥20 hard-negative decline set, S6 positive-identity router rule, N1 decline
logging, N2 unified wording. Ready to plan.
**Relates to:** `2026-06-16-rerank-retrieval-design.md`, `project_retrieval_architecture`,
`project_day_to_day_intents` (this is the "retrieval safety layer" that must precede scaling
the KB to the 150 day-to-day intents, many of which are high-stakes: visa, billing, deadlines)

## Problem & Evidence

The 100-question eval (post-rerank) exposed two downstream failure modes that retrieval
precision alone does not fix:

1. **Confident-wrong / should-decline.** When retrieval is weak, the bot dresses up a
   weakly-relevant chunk as an answer instead of declining: "how do I get a parking permit"
   → returned an unrelated GSA Highlander-Hub page; "how do I drop a class" → returned OGI
   F-1 enrollment rules. For the upcoming high-stakes content (visa/billing/deadlines), a
   confident-wrong answer is the worst outcome.
2. **Router over-trigger.** "Who can **impeach** a GSA **officer**" is hijacked by the
   `officers_in_org` structured skill (the bare word "officer" matches `_OFFICER`), so it
   returns the officer roster instead of the constitution's impeachment rule.

This increment is the **retrieval safety layer**: decline-and-route when we don't
confidently have the answer, and stop the router from hijacking process questions. It is the
prerequisite for safely scaling the KB.

**Explicitly out of scope** (separate next increments): generation-quality misses where the
right chunk WAS retrieved but the LLM flubbed it (who-chairs-GA, term-limits, food-cost-for-25),
and smart per-topic office routing (the cat-M office-routing pilot). `decline_route` is built
as the seam the latter plugs into.

## Two independent components

### Component B — Router precision (`v2/core/retrieval/router.py`)

The `officers_in_org` route fires whenever an officer term ("officer/president/vp/e-board/…")
appears anywhere AND an org resolves. Fix by **structure, not substring** (same approach as the
`reset`→clear-history fix), using a **positive identity requirement rather than a verb denylist**
(senior review S6 — a denylist leaks "who can *nominate* an officer", "how is an officer
*chosen*", etc.). Route to `officers_in_org` **only** when the question matches an identity
pattern asking *who holds the seat*:
- `who (is|are|'s) [the] … <officer-title>` (President / VP … / officers / e-board), or
- `(list|name|show) [the] … officers`.

Everything else falls through to RAG — the router's existing safe default — so any unforeseen
process phrasing ("who can impeach a GSA officer", "what are the duties of the VP", "how do I
become an officer", "who is eligible to be an officer") is handled by the constitution via RAG.
Pure-deterministic; can only make routing *more* conservative. The positive pattern must be
tested against contractions ("who's the VP of Finance") and bare "list the GSA officers"
(senior review S7), plus the existing `test_router_officers.py` positives must still pass.

### Component A — Answerability gate

**Signal (data-decided, margin-first — senior review C1).** `sigmoid(CE)` from
`ms-marco-MiniLM` is a *ranking* score, monotonic within a query but **not a calibrated,
cross-query absolute relevance** — its magnitude drifts with phrasing/length, so a single fixed
absolute threshold would systematically false-decline real answers and pass confident-wrong
ones (the high-stakes failure we're preventing). Instead the gate uses a **margin / "does one
chunk stand out" signal that is query-stable**: the top-1 CE logit minus the k-th (or median)
CE logit of the reranked pool. An in-corpus question yields a clear standout top-1; an
out-of-scope question (parking, drop-class) yields a flat, low pool where nothing stands out.

The signal is **chosen by the diagnostic, not assumed**: `_answerability_diagnostic.py` logs,
per query, BOTH the absolute `sigmoid(CE)` of the best real chunk AND the top1−median margin
(raw-logit space). We pick whichever cleanly separates the answerable vs decline sets; if only
the margin separates (expected), the gate uses the margin. We do **not** commit to the absolute
threshold before the diagnostic runs.

**Plumbing:**
- `RetrievedChunk` gains `rerank_score: float | None` (raw CE logit for chunks the reranker
  scored; `None` for keyword-only / expanded chunks). `_rerank` sets it.
- `_rerank` also computes the **per-query `answerability` margin** over the windowed pool
  (top-1 − k-th logit) and threads it out of `retrieve()`; the shim attaches it to **every**
  returned `V1Chunk` (`V1Chunk.answerability`) so the value survives `_diversify_and_expand`
  reordering and an expanded `profile` landing at `chunks[0]` (senior review S1).

**Gate — wraps the shared generation point** (`message_handler` `if chunks and self.ollama:`
block, ~L410, NOT a single branch), so it also covers the SOCIAL branch and the retry path
(senior review S4):
```
ans = next((c.answerability for c in chunks if c.answerability is not None), None)
if ans is not None and ans < ANSWERABILITY_MIN:
    log_decline(query, ans)            # always-on (senior review N1)
    return decline_route(query)        # do NOT generate from weak context
# else: generate as today
```
- Fires **only when a real margin is present**. Reranker unavailable → `answerability is None`
  → gate inert → behavior identical to today. The gate can only *decline*, never worsen a good
  answer.
- `decline_route(query)` returns the GSA-contact decline, unified with the existing "no chunks"
  deflection wording (senior review N2) so the two paths don't drift. It is the seam the cat-M
  office-routing skill later replaces with per-topic routing.
- **`ANSWERABILITY_MIN` is read per-call** (senior review S2/S3): `V2RetrieverShim._retrieve_sync`
  already opens a `get_connection`; it reads `retriever.answerability_min` there (one `SELECT`,
  matching the `_load_*` pattern) so flipping it to `0` in `settings` is an **instant**
  kill-switch with no restart. The effective threshold is threaded out alongside `answerability`
  (e.g. the shim returns/attaches both), not read by the handler via `getattr` duck-typing.

## Calibration (measured, not guessed)

`scripts/_answerability_diagnostic.py` logs, per query, BOTH signals (absolute `sigmoid(CE)`
of the best real chunk AND the top1−median margin) for two labeled sets:
- **answerable** — the GOLD + GUARD questions from `v2/tests/rerank_gold.py`. **Note (senior
  review C2): "Who can impeach a GSA officer…" is a GOLD question — it is *answerable* from the
  constitution (Component B routes it to RAG), NOT a decline.** The router fix and the gate must
  agree on this: impeach → answer.
- **should-decline (≥20, hard negatives — senior review S5)** — not just easy campus-life
  out-of-scope (parking, wifi, gym, spring break, pay tuition) but **GSA-adjacent-but-
  unanswerable** ones that sit near the boundary: "how do I drop a class", "how do I get a
  parking permit", "what's the deadline to add a course", "how do I register for classes", "how
  do I reset my NJIT password", "where is on-campus housing", "how do I waive health insurance",
  "what are the dining hall hours", etc. (the spec's own example, drop-class→visa-rules, is a
  hard negative and must be in the set).

Pick the signal + threshold giving **zero false-declines on the answerable set** while declining
the maximum of the should-decline set, and **report the separation margin** (not just a pass).
If neither signal cleanly separates (esp. the hard negatives), that is a finding — surface it
and discuss before shipping; we will not ship a gate that declines real answers.

## Error handling

| Condition | Behavior |
|---|---|
| Reranker unavailable / `rerank_score is None` | gate inert → generate as today |
| `ANSWERABILITY_MIN = 0` | gate never fires (kill-switch) |
| No chunks at all | existing "couldn't find / contact GSA" path, unchanged |
| Router: ambiguous question | falls through to RAG (existing safe default) |

## Testing & acceptance (deterministic; no LLM in the gates)

**Component B — router** (`v2/tests/test_router_precision.py`): parametrized
- routes → `officers_in_org`: "who are the GSA officers", "who is the GSA president",
  "list the GSA officers", "who's the VP of Finance".
- falls through (route is None): "who can impeach a GSA officer", "what are the duties of the
  VP of Finance", "how do I become a GSA officer", "how many officers are there",
  "who is eligible to be an officer".

**Component A — answerability** (`v2/tests/test_answerability_gate.py`), chunk-level, no generation:
- answerable set (incl. impeach): `answerability ≥ ANSWERABILITY_MIN` (would answer) — **0 false-declines**.
- should-decline set (≥20 hard negatives): `answerability < ANSWERABILITY_MIN` (would decline).
- unit: `decline_route(q)` returns the GSA contact; gate inert when `answerability is None`; the
  decline picks the best-real score across the pool, not positional `chunks[0]` (S1).

**Acceptance bar:**
| Metric | Target |
|---|---|
| Router tests | all green (identity routes; impeach/duties/become/eligible fall through) |
| Answerability separation | declines the ≥20 out-of-scope, **0 false-declines** on answerable, **reported margin** |
| End-to-end smoke (100-Q) | parking/drop-class/wifi now decline+route; **impeach answers from the constitution** |

## Files

- Modify `v2/core/retrieval/router.py` — officer route structural guard.
- Modify `v2/core/retrieval/retriever.py` — `RetrievedChunk.rerank_score`; `_rerank` sets it.
- Modify `v2/integration/retriever_shim.py` — carry `rerank_score` onto `V1Chunk`.
- Modify `bot/core/message_handler.py` — the gate guard + `decline_route` (or a small helper module).
- Create `scripts/_answerability_diagnostic.py` — threshold calibration.
- Create `v2/tests/test_router_precision.py`, `v2/tests/test_answerability_gate.py`.

## Out of scope (separate increments)
- Generation-quality misses (right chunk retrieved, LLM flubbed) — prompting/answer-verification.
- Smart per-topic office routing (cat-M office-routing pilot) — `decline_route` is its seam.
