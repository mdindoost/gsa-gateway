# A15b — topic→people trustworthiness: never assert a non-NJIT person (+ A11 + A15 determiner)

**Date:** 2026-07-04
**Status:** Fable design-review = **APPROVE-WITH-CHANGES → folded in** (R1 live-reinvoke control flow, R2
scope-narrowing named, R3 un-tagged eval Qs, O1 plumbing wording, O2 keep-neutral-context, flag terminal
state). → **awaiting owner sign-off** → build TDD → Fable diff → ship.
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

## Scope narrowing — NAMED, not dropped (Fable R2, review-against-plan hard line)
The roadmap's A15b was framed as "wrong-**TOPIC** people" (a CE-relevance guard). This spec deliberately pivots
to a **non-NJIT-IDENTITY** guard, because the execution evidence falsified the relevance framing (the polluting
seminar chunk is topically ON-target; only the person is non-NJIT). **Accepted residual:** the identity guard
does NOT catch a *real NJIT person who is on-keyword but does not actually study the topic* — that person
carries an `entity_id`, survives the trim, and could be asserted. This is the original "wrong-topic" case,
knowingly narrowed out of the enforcing path here. It is a smaller, defensible risk (survivors are the
highest-CE stamped chunks; Commit 2 routes most tagged topics to the KG and away from RAG entirely; compose is
grounded at temp 0.3), and it is **covered by Commit 4's topic-fit name-verify TELEMETRY** — promotion to an
enforcing topic-fit check is an explicit deferred item, not a silent drop.

## Scope — three coordinated changes, one branch, sequenced commits

### Commit 0 (build-time, no ship) — linkage-coverage audit (Fable Q4) — ✅ DONE 2026-07-04
Ran the REAL retriever over 8 person-seeking RAG queries (scratchpad/a15b_audit2.py), reading `entity_id` from
the DB by `item_id` (the shim strips it today). Result over 40 top-5 chunks:
- **45% STAMPED** (DB `entity_id`) → guard KEEPS — every one a real NJIT person (Kallioniemi, Garnier, Swissler,
  Crespo, Ancis, Oza, Venerus, Buffone, Mahgoub).
- **0% unstamped person-ish type** → **ZERO demote-risk.** Every profile/about/research_statement/teaching/
  research_areas chunk carried a stamp; there is NO loose-NJIT-person-prose-without-a-stamp in these pools.
- **55% unstamped non-person** (pdf/policy/faq/"Click Here") → safe HARD DROP.
**GATE RESULT → HARD DROP (not soft-demote).** Because person-about chunks are 100% stamped, the guard hard-drops
unstamped chunks with no coverage loss — the softer demote-below-survivors fallback is NOT needed. Queries with 0
stamped chunks in the top-5 (e.g. "who studies memory and cognition") → the ≥1-entity activation rule stands the
guard down → normal ladder handles them. One accepted edge: a `faq`-type "Who is Prof. X?" chunk is unstamped and
would be dropped, but the person's own stamped profile chunk carries the answer, so no NJIT person is lost. The
0% person-ish-unstamped number is ALSO the evidence that the deeper "extend entity-linkage to loose prose" data
thread is NOT needed now.

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
The leak is ONLY in the **loose-verb branch** (`_LOOSE_CONNECTOR`, `router.py:~486`), which deliberately keeps
the determiner — the topic-first branch already strips via `_DET_LEAD` (`:482`). Fix at the **validation site**
(`route ~640`, NOT in `_extract_area_loose` — do not double-strip): where the loose candidate `_loose` is
checked against `is_listed_research_area`, ALSO try its **leading-determiner-stripped** form (`the|a|an`) and
route if EITHER validates. `is_listed_research_area` remains the sole guard (word-boundary match on real tag
VALUES), so this only ever ADDS recall for candidates that are genuinely listed tags — worst case is unchanged
(→ RAG). Fixes "which professors study **the** brain" → `"brain"` → KG (11 people). Preserves A15's R2 intent:
"faculty in the news" → "news" → not a tag → RAG.
- **Regression:** the full A15 router/skills suite must stay green; add the "which professors study the brain"
  family + hard-negatives ("in the news", "in the department", "for the semester") to the tests.
- Ships **unflagged** (pure validation-guarded recall add, like A15 itself), eval-Q backed.

### Commit 3 — A15b entity-scoped compose guard (the safety net)
**(a) Plumb the signal (two small adds — O1).** `_meta_entity_id` exists (`retriever.py:196`) but is NOT wired
onto the chunk: (i) add `entity_id: str | None = None` to `RetrievedChunk` and populate it at the build site
(`retriever.py:~607`) with `entity_id=_meta(r["metadata"]).get("entity_id")`; (ii) extend the hand-picked
`V2RetrieverShim._to_v1` metadata dict (`retriever_shim.py:~159`) with `"entity_id": getattr(c,"entity_id",None)`
so the compose boundary reads `chunk.metadata["entity_id"]`. (No DB change; the value already exists in
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

**Flag terminal state (Fable):** both `MISS_SIGNAL_SKIP_UNSCORED` and `PERSON_SCOPE_GUARD_ENABLED` ON is the
intended end state — they are split only for independent eval-diffing, not a permanent either/or. Rollout is
robust to flag ORDER: guard-ON compensates for A11-OFF (it trims the McGill chunk out of a spuriously-rescued
pool), and A11-ON removes the spurious rescue that produced the pool — so neither ordering leaves the repro
exposed.

**(d) Degrade policy** when the trim runs (Fable-default; low-stakes now that Commit 2 removes the flagship
query from this path — it only governs the open-vocab tail):
1. **≥1 NJIT-person chunk survives** → compose from the survivors (**keep** neutral topic-context chunks — O2:
   they carry no false-person risk and thinning the answer buys zero safety). Honest-partial: answer from who
   we can confirm.
2. **Zero person chunks survive** (predicate fired, ≥1 entity chunk existed, but none resolved) → **re-invoke
   the live tier via the existing `self.live_search(base_q)` seam** (A1-gated), inline at the guard site
   (Fable R1, option (a) — reuse the one live seam, do not duplicate it). **Control-flow hole this closes:**
   the guard runs AFTER the fallback ladder, so if `primary_miss` was False (a good primary pool, ladder never
   ran) and the guard *then* trims to zero, live would otherwise never have been tried — a reachable real
   answer silently skipped (a never-withhold violation). So the guard OWNS the zero-survivor live attempt; set
   `attempted_live=True` and route its `LiveAnswer`/`LiveLinks`/`None` through the SAME consumer mapping as the
   ladder's live sites (B3 from A1: `LiveLinks` → `is_abstain`/`live-offtarget`/`is_live=False`).
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
- **Eval Qs:** add "which professors study the brain" (verifies Commit 2 → KG, 11 people) PLUS 3–4 sibling
  topic→people queries whose topic is **genuinely un-tagged** so they hit RAG + the guard (Fable R3 — otherwise
  the guard has zero real-traffic coverage, only synthetic unit tests). **Build step:** for each candidate,
  confirm via `ask.sh` that it routes to RAG (not KG) BEFORE adding it; a query that routes to KG doesn't
  exercise the guard. Record the chosen un-tagged topics in the PR.

## Goals checklist (to verify at ship)
- A11 miss-signal skip-unscored + eval diff — IN (Commit 1, flagged)
- A15 determiner-stripped validation (flagship completeness → KG, 11 people) — IN (Commit 2, unflagged)
- entity_id plumbed to compose boundary — IN (Commit 3a)
- shared person-seeking predicate w/ contact-exclusions — IN (Commit 3b)
- entity-scoped trim at compose choke-point, activation rule, fail-open — IN (Commit 3c, flagged)
- honest-partial → live (guard-owned re-invoke, R1) → abstain degrade — IN (Commit 3d)
- linkage-coverage audit gating guard aggressiveness — IN (Commit 0)
- post-compose name-verify TELEMETRY-ONLY — IN (Commit 4)
- **NARROWED (named, not dropped — R2):** roadmap's "wrong-TOPIC people" → this ships a non-NJIT-IDENTITY guard;
  topic-fit verification of surviving *NJIT* persons is DEFERRED to Commit 4 telemetry (accepted residual above)
- seminar/colloquium page typing at the crawler — DEFERRED (redundant negative signal; data producer is
  already correct; not required for the root fix)
- brain→neuroscience synonym-UNION (widen "brain" to also return the neuroscience cluster) — DEFERRED
  (separate recall enhancement; "brain" already returns 11 real people)
- promote name-verify to enforcing — DEFERRED (needs measured false-positive rate)
- opt-in "full list" hint on honest-partial (owner fork) — DEFERRED
