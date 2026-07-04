# A15b — topic→people trustworthiness: never assert a non-NJIT person (+ A11 + A15 determiner)

**Date:** 2026-07-04
**Status:** DRAFT → Fable design-review → owner sign-off → build TDD → Fable diff → ship.
**Roadmap:** accuracy review (`project_pipeline_accuracy_review`), items **A15b** (RAG asserts wrong-topic/non-NJIT
people) bundled with **A11** (distorted miss-signal — the trigger) and an **A15 determiner fix** (the
completeness half). Fable design-consult adjudicated the scope (2026-07-04).

## The failure (EXECUTION-PROVEN, live pipeline via ask.sh, 2026-07-04)
Query **"which professors study the brain"** → routes to RAG → the composed answer is *entirely* about
**Dr. Yasser Iturria Medina, Assistant Professor at the Montreal Neurological Institute, McGill** — an
**external seminar visitor** whose talk abstract was ingested — presented as the answer to *which NJIT
professors* study the brain. The real NJIT brain researchers (Elisa Kallioniemi CE=0.858, Xin Di) are dropped.
This is a **false attribution** of NJIT-faculty identity to a non-NJIT person (hard-line #4: compose MUST NOT
add/attach an unlisted attribute to a name).

### Two independent root causes collide
1. **A11 — distorted miss-signal (the trigger).** `top_relevance` reads `chunks[0]`, which is an *injected
   profile card* with no `ce_score` → it reports top relevance 0.000 → `primary_miss=True` even though the real
   top chunk is CE=0.858 → the deep-fallback fires and rescues the seminar parent page (`rescue_rel=0.906`) →
   compose from the visitor. (With deep-fallback OFF, the same false miss instead fires live, which happens to
   return a *correct* biology.njit.edu roster — so the same query is severely-false or correct purely by which
   fallback A11 mis-triggers.)
2. **Corpus pollution — a data/retrieval ALIGNMENT gap, on the RETRIEVAL side only.** The KB legitimately holds
   seminar/colloquium pages naming external speakers; they rank high on topic. **But the data already encodes
   the distinction:** person chunks carry `metadata.entity_id → people.njit.edu/profile/<slug>` (a real NJIT
   `nodes` Person); seminar/external pages carry **none** (verified on the exact repro chunks). Retrieval throws
   that signal away and lets an unstamped seminar chunk stand in as the person answer.

### Why the flagship query even reached RAG (the completeness root)
`route("which professors study the brain")` returns **None** (→ RAG), yet the KG can answer it completely:
`people_by_research_area("brain")` returns **11 real NJIT people** (Kallioniemi, Xin Di, Severi, Pfister,
Biswal…). The router extracts the candidate `"the brain"` (loose-verb branch keeps the determiner), and
`is_listed_research_area("the brain")` is **False** (tag values read "Brain Imaging", not "the brain"), while
`is_listed_research_area("brain")` is **True**. So a single determiner sends a fully-answerable query to the
polluted RAG path.

## Design principle (owner: "cure from the root"; hard line: fix data producer AND retrieval TOGETHER)
Fable's ruling: **the data producer is already correct** — the crawler stamped Kallioniemi with an `entity_id`
and correctly gave the McGill visitor none (there is no NJIT person to stamp). The one-standard *"identity is
carried by node linkage"* already lives in the data; the defect is entirely retrieval-side, which discards it.
So "align data + retrieval" here means **making retrieval honor the standard the data already sets** — no DB
change is needed for the guard to be correct, because the DB is already correct. The two coordinated fixes
cover what the other structurally cannot:
- **Determiner fix (routing/completeness):** makes *covered* topics route to the KG deterministically →
  correct **and complete** (the 11 people), never touching RAG. This is the actual cure for the flagship query.
- **Entity-guard (retrieval/safety):** the irreducible safety net for the **open-vocabulary tail** — every
  topic→people query for which no research-area tag will ever exist — so a seminar/external chunk can never be
  asserted as the person answer.

## Scope — three coordinated changes, one branch, sequenced commits

### Commit 0 (build-time, no ship) — linkage-coverage audit (Fable Q4)
Over the person-seeking questions in `eval/questions.txt`, measure the fraction of top-5 retrieved chunks that
are *about* an NJIT person yet **lack** an `entity_id`. This (a) calibrates how aggressive the guard can safely
be, and (b) quantifies the deeper data gap ("extend entity-linkage to loose prose"). **Gate:** if coverage is
high → ship the guard as designed (Commit 3). If low → the guard demote must be *soft* (see Commit 3 degrade)
and the audit number becomes the evidence for a separate data-linkage thread. The audit result is recorded in
this spec before Commit 3 is built.

### Commit 1 — A11: miss-signal reads the first SCORED chunk
`V2RetrieverShim.top_relevance` (`retriever_shim.py:110`) must **skip chunks with no real `ce_score`** (the
injected profile card is unscored, NOT irrelevant — do NOT read it as 0.000) and use the first chunk that
carries a `ce_score` (metadata `ce_score`, else the `ce_score` attr). Only if NO chunk is scored does it fall
back to a single reranker pass on the first chunk (today's tail path).
- **Blast radius (Fable risk 3):** this shifts a whole class of person-topic queries out of spurious
  deep-fallback/live back to KB compose. **Gate:** run `eval.sh`, diff the kb/live/deflect classification
  per question vs baseline; any question flipping live→kb is spot-checked for answer-quality regression.
- **Flag:** `MISS_SIGNAL_SKIP_UNSCORED` (default **off** = today). Flip after the eval diff is clean.

### Commit 2 — A15 determiner fix: validate the determiner-stripped candidate too
In `router.route`, where the loose-area candidate is validated against `is_listed_research_area`, ALSO try the
**leading-determiner-stripped** form (`the|a|an`) and route if EITHER validates. `is_listed_research_area`
remains the sole guard (word-boundary match on real tag VALUES), so this only ever ADDS recall for candidates
that are genuinely listed tags — worst case is unchanged (→ RAG). Fixes "which professors study **the** brain"
→ `"brain"` → KG (11 people). Preserves A15's R2 intent: "faculty in the news" → "news" → not a tag → RAG.
- **Regression:** the full A15 router/skills suite must stay green; add the "which professors study the brain"
  family + hard-negatives ("in the news", "in the department", "for the semester") to the tests.
- Ships **unflagged** (pure validation-guarded recall add, like A15 itself), eval-Q backed.

### Commit 3 — A15b entity-scoped compose guard (the safety net)
**(a) Plumb the signal.** Add `entity_id: str | None` to `RetrievedChunk` (populated from the metadata the
retriever already parses via `_meta_entity_id`), and carry it through `V2RetrieverShim._to_v1` into
`V1Chunk.metadata["entity_id"]` so the compose boundary can read it. (No DB change; the field already exists in
`knowledge_items.metadata`.)

**(b) Shared person-seeking predicate.** A single module-level predicate (factored from the router's existing
`_FACULTY_CUE`/`_PERSON_INTENT` shapes — **reuse, do not duplicate the regexes**, per Fable): person-seeking =
`(who|which|what) … (professor(s)|faculty|researcher(s)|prof(s)|instructor(s)|PI)`. **Explicit exclusions**
(the biggest false-positive class): contact/office shapes — "who do I contact/talk to/email about X", "whose
office handles X" — must NOT fire. Hard-negatives written FIRST (TDD).

**(c) The choke-point.** In `message_handler._rag_pipeline`, AFTER the fallback ladder has settled the final
chunk set and BEFORE `generate_answer` (so it uniformly covers the primary pool, the deep-fallback rescue, AND
office chunks — a retriever-internal guard would miss the rescue path that caused this very bug). When the
predicate fires:
- **Activation rule (anti-blast-radius):** only trim when **≥1 chunk in the pool carries an `entity_id`**.
  Predicate fires but zero person-entity chunks exist → **do NOT trim** (pass through; the query is answered by
  prose/live/abstain as today). This guarantees the guard is structurally incapable of touching non-person
  prose RAG (funding/policy/how-to) and never trims a pool to empty.
- **Trim:** keep chunks that carry an `entity_id` resolving to an active NJIT `Person` node; drop the
  identity-unstamped chunks (seminar PDFs, external-visitor pages). Neutral non-identity context chunks (a
  topic policy/overview page) MAY be kept — they carry no false-person risk. (If the Commit 0 audit shows low
  linkage coverage, the drop becomes a **demote-below-survivors** rather than a hard drop, to avoid dropping
  real-NJIT-person prose that simply lacks a stamp.)
- **Fail-open (hard line — never break the answer path):** any exception, missing metadata, or KG-lookup
  failure → pass the chunk through untouched. The guard only ever REMOVES a demonstrably-unstamped chunk.
- **Flag:** `PERSON_SCOPE_GUARD_ENABLED` (default **off** = today; byte-identical when off).

**(d) Degrade policy** when the trim runs (Fable-default; low-stakes now that Commit 2 removes the flagship
query from this path — it only governs the open-vocab tail):
1. **≥1 NJIT-person chunk survives** → compose from the survivors (+ neutral context chunks). Honest-partial:
   answer from who we can confirm.
2. **Zero person chunks survive** (predicate fired, ≥1 entity chunk existed, but none resolved) → fall to the
   **live tier** (now A1-gated) — proven to produce the right shape for exactly this query class.
3. **Live misses / disabled / capped** → honest abstain (`_useful_abstain` + the strongest source link in the
   pool). **Never** name the external seminar speaker (not even with a "McGill" disclaimer in v1).
- **Owner fork (deferred, non-blocking):** whether case 1 additionally appends an opt-in "want the full NJIT
  list? → search njit for X" hint (the A1 cross-platform hint). Default: no hint; revisit if the tail proves
  thin in practice. The determiner fix means the flagship query never reaches here.

### Commit 4 (optional, telemetry-only) — post-compose KG name-verify (LOG ONLY)
On person-seeking answers, extract capitalized name spans and check them against `persons_by_lastname`; **log**
misses to analytics. Name-matching against generated prose is fragile, so it is telemetry-first — it does NOT
gate or alter any answer in this spec. Promotion to enforcing is a separate, evidence-backed decision.

## Invariants / safety
- **Flag-off = today, byte-identical:** all three behavior-changing pieces default off (`MISS_SIGNAL_SKIP_
  UNSCORED`, `PERSON_SCOPE_GUARD_ENABLED`) or are pure validation-guarded recall (determiner, → RAG worst case).
- **Never break the answer path / never-withhold:** the guard is fail-open and never trims to empty; the
  degrade ladder ends in live then honest-abstain, never a crash or a silent drop of the only content.
- **Never fabricate / honest-partial:** the guard IS this rule applied to identity — answer from confirmed NJIT
  people, say nothing about those we can't confirm; never assert an external person as NJIT faculty.
- **NJIT-verbatim / GSA-equal:** untouched — we select which chunks compose, we do not edit content, and no org
  is privileged.
- **Data producer unchanged:** the crawler is already correct; this spec adds NO crawl/DB change (seminar
  typing is explicitly deferred).

## Blast radius
Non-person RAG (funding/policy/how-to) is structurally untouched: the predicate doesn't fire, and even if it
did, the ≥1-entity-chunk activation rule means a pure-prose pool is never trimmed. A11 shifts fallback
frequency (eval-diff-gated). The determiner fix only adds KG recall for real tags.

## Tests (TDD)
- **A11:** injected-profile-card-at-rank-1 pool → `top_relevance` returns the first scored chunk's CE (0.858),
  not 0.000; all-unscored pool → today's reranker-pass fallback; flag-off → reads `chunks[0]` as today.
- **Determiner:** "which professors study the brain" → KG `people_by_research_area(area="brain")`; hard-negs
  "faculty in the news / in the department / for the semester" → RAG; full A15 suite stays green.
- **Guard:** person-seeking + a pool mixing a stamped person chunk (entity_id) and an unstamped seminar chunk →
  compose sees only the stamped chunk; zero-entity pool → no trim (pass through); contact/office query → guard
  doesn't fire; exception in the KG lookup → fail-open (pass through); flag-off → no trim.
- **Degrade:** all person chunks fail to resolve → live tried, then abstain; never names the external speaker.
- **Regression:** full bot/tests live/gate/router/skills suites; eval.sh kb/live/deflect diff vs baseline.
- **Eval Qs:** add "which professors study the brain" + 3–4 sibling topic→people queries to `eval/questions.txt`
  as permanent regressions (per grow-correctness-suite).

## Goals checklist (to verify at ship)
- A11 miss-signal skip-unscored + eval diff — IN (Commit 1, flagged)
- A15 determiner-stripped validation (flagship completeness → KG, 11 people) — IN (Commit 2, unflagged)
- entity_id plumbed to compose boundary — IN (Commit 3a)
- shared person-seeking predicate w/ contact-exclusions — IN (Commit 3b)
- entity-scoped trim at compose choke-point, activation rule, fail-open — IN (Commit 3c, flagged)
- honest-partial → live → abstain degrade — IN (Commit 3d)
- linkage-coverage audit gating guard aggressiveness — IN (Commit 0)
- post-compose name-verify TELEMETRY-ONLY — IN (Commit 4)
- seminar/colloquium page typing at the crawler — DEFERRED (redundant negative signal; data producer is
  already correct; not required for the root fix)
- brain→neuroscience synonym-UNION (widen "brain" to also return the neuroscience cluster) — DEFERRED
  (separate recall enhancement; "brain" already returns 11 real people)
- promote name-verify to enforcing — DEFERRED (needs measured false-positive rate)
- opt-in "full list" hint on honest-partial (owner fork) — DEFERRED
