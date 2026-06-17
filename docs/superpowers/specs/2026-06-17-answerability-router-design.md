# Answerability Gate + Router Precision — Design

**Date:** 2026-06-17
**Status:** Approved (design); pending senior-eng review before build
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
appears anywhere AND an org resolves. Fix by **structure, not substring** (same approach as
the `reset`→clear-history fix): route to `officers_in_org` only for an **identity ask** —
"who is/are the … officer(s)/president/vp", "list/name the officers" — and **never** when an
action/relational verb makes the officer the object: impeach, remove, elect, appoint, become,
eligible, dismiss, replace, duties, responsibilities, role. Those fall through to RAG (the
router's existing safe default). Pure-deterministic; can only make routing *more*
conservative, so it cannot introduce a new wrong-route.

### Component A — Answerability gate

**Signal:** the cross-encoder's absolute relevance score (chosen over cosine similarity —
poorly calibrated for "answers the question" — and an LLM self-check — non-deterministic, weak
8B judge). `_rerank` already computes per-chunk CE scores and currently discards them.

**Plumbing:**
- `RetrievedChunk` gains `rerank_score: float | None` (= `sigmoid(CE)` for chunks the reranker
  scored; `None` otherwise).
- `_rerank` sets it on each windowed chunk.
- `V2RetrieverShim._to_v1` carries it onto `V1Chunk.rerank_score`.

**Gate (in `bot/core/message_handler.py`, RAG branch, before generation):**
```
top = chunks[0]
if top.rerank_score is not None and top.rerank_score < ANSWERABILITY_MIN:
    return decline_route(query)     # do NOT generate from weak context
# else: generate as today
```
- Fires **only when a real CE score is present**. If the reranker is unavailable
  (`rerank_score is None`), the gate is inert → behavior identical to today. The gate can only
  *decline*, never worsen a good answer.
- `decline_route(query)` — a small single-purpose function returning the GSA-contact decline
  ("I don't have a confident answer on that in the GSA knowledge base. Contact the GSA at
  gsa-pres@njit.edu or visit Campus Center 110A, weekdays 11AM–5PM."). This is the seam the
  cat-M office-routing skill later replaces with per-topic routing.
- `ANSWERABILITY_MIN` is admin-tunable via the `settings` table key
  `retriever.answerability_min` (default = the calibrated value below; `0` = never decline =
  kill-switch). To keep the handler decoupled from the v2 settings layer, **`V2RetrieverShim`
  reads it once at construction** (it has `db_path`; same `get_connection` it already uses) and
  exposes it as `self.answerability_min`. The gate reads
  `min_score = getattr(self.retriever, "answerability_min", 0.0)` — so the v1 retriever (no such
  attribute) and any future retriever default to `0.0`, i.e. gate inert.

## Calibration (measured, not guessed)

A pre-build diagnostic (`scripts/_answerability_diagnostic.py`, like the recall diagnostic)
logs the **top chunk's `rerank_score`** for two labeled sets:
- **answerable** — the GOLD + GUARD questions from `v2/tests/rerank_gold.py` (must stay ≥ threshold).
- **should-decline** — out-of-scope: parking permit, drop a class, wifi password, campus gym,
  spring break dates, pay tuition bill (must fall < threshold).

Pick the highest threshold that yields **zero false-declines on the answerable set** while
declining all (or the maximum of) the should-decline set. If the two distributions overlap
with no clean separator, that is a finding — surface it and discuss before shipping (the CE
score alone may be insufficient; we would not ship a gate that declines real answers).

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
- answerable set: top `rerank_score ≥ ANSWERABILITY_MIN` (would answer) — **0 false-declines**.
- should-decline set: top `rerank_score < ANSWERABILITY_MIN` (would decline).
- unit: `decline_route(q)` returns the GSA contact; gate inert when `rerank_score is None`.

**Acceptance bar:**
| Metric | Target |
|---|---|
| Router tests | all green |
| Answerability separation | declines the 6 out-of-scope, **0 false-declines** on answerable |
| End-to-end smoke (100-Q) | parking/drop-class/wifi now decline+route; "impeach" answers from the constitution |

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
