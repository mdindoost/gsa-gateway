# LLM-Verified Area Expansion — Design Spec

**Date:** 2026-07-07
**Branch:** feat/processing-debt-pilot (or a fresh `feat/area-expansion` — decide at build)
**Status:** DESIGN — awaiting Fable (RAG) + senior-eng review → owner sign-off → TDD build
**Origin:** Processing-Debt project (`docs/research/oracle-processing-debt/`). The pilot's robust finding was
~85% POOL-dominated debt (owned facts we never surface). Owner decision (2026-07-07): stop measuring, fix the
bot. First fix chosen: umbrella research-area queries surface 1-of-N owned experts.

---

## 1. Problem (proven live)

Query **"who is working on cyber security"** → bot answers **"1 faculty: Chase Wu."** We actually own **15
faculty** whose research areas contain "security," fragmented across 15 separate `ResearchArea` nodes
(`network security`, `cloud security`, `system security`, `wireless security`, `cybersecurity` (one word, a
*different* node than `cyber security`), …). The router routes correctly to
`people_by_research_area(area='cyber security')`; the skill (`skills.py` `_research_entities` → `_fts_query` →
`expand_area`) does **exact-phrase FTS matching**, so the umbrella term matches only the literal-phrase tag →
**1 of 15 surfaced (~93% of owned experts dropped).** This generalizes to every umbrella topic (ML, AI,
networks, databases). It is the flagship of the pilot's 86 POOL owned-misses.

### Why the two naive fixes fail (measured, not guessed)
- **Token/head-noun broadening + hardcoded safe-head allowlist** — rejected by owner (hardcoding a taxonomy is
  perpetual manual upkeep). Also unsafe unguarded: measured on our corpus, head `security` is clean (14 tags,
  all security) but head `systems` pulls 59 tags across transportation/power/healthcare/operating/biological,
  `networks` pulls neural+social networks, `learning` pulls motor-learning + service-learning.
- **Bare embedding-cosine threshold** — measured on our embeddings (qwen3-embedding:0.6b): a *real sibling*
  (`operating systems`↔`distributed systems` = 0.40) scores **lower** than a *false friend*
  (`computer networks`↔`neural networks` = 0.56). No single global cutoff separates the classes. (Embeddings
  DO help on some traps — `machine learning`↔`service-learning` = 0.18 — but not reliably alone.)

Only *meaning-aware judgment* separates "network security belongs with cyber security" from "neural networks
does NOT belong with computer networks." Hence: **LLM verify.**

## 2. Goal & non-goals

**Goal:** an umbrella (or specific) research-area query surfaces *all and only* the faculty in that field,
by letting an LLM decide which of **our owned area tags** belong to the queried field. No hardcoded taxonomy;
generalizes to any field; cannot invent people or areas (the LLM only ever selects from tags we own).

**Non-goals:** changing the router; changing how any non-research skill works; building an offline area
ontology; fixing the KG-vs-KB winner-take-all merge (a separate, larger lever — parked, see the project memory
"Other 86-miss clusters"); de-duplicating the fragmented `ResearchArea` nodes themselves (data-cleanup, separate).

## 3. Mechanism

New module `v2/core/retrieval/area_expand.py`. A single entry point, called only by the enumerate skills:

```
expand_area_llm(conn, area) -> set[str]   # returns VERIFIED owned tag strings (exact tag values)
```

Pipeline inside it:

1. **Candidate shortlist (embeddings, recall-only):** embed the query `area`; cosine-KNN against our **distinct
   area-tag vocabulary** (~1,379 short tag strings), take top-K (default **30**). Deliberately loose — precision
   is the LLM's job. Vocabulary + its embeddings are built once and **cached, keyed to a vocabulary hash** (the
   tag set changes only on crawl/edit).
2. **LLM verify (precision):** hand the LLM the query field + the K candidate tags as a numbered list; it returns
   the indices of tags that are the **same research field** (subfields count; merely-related-but-different fields
   do NOT). Temp 0, output constrained to a list of integers → mapped back to tag strings. The LLM sees only tag
   strings from our vocabulary, so its output is a subset of owned tags — it cannot hallucinate.
3. **Deterministic SQL (unchanged):** the caller unions today's exact-phrase result with the people who list any
   verified tag:
   `entities = _research_entities(conn, area, org_id)  ∪  {people who list any tag ∈ verified}`
   (the second set via the existing `metadata.areas` tag-match path, org-scoped by the same SQL). **Strictly
   additive** — every person surfaced today is still surfaced; the fix only *adds* the fragmented siblings.

`count_people_by_research_area` uses the SAME expanded entity set (they already share one function), so list and
count can never disagree.

### 3.1 Prompt (verify step)
```
You are given a RESEARCH FIELD and a numbered list of specific research-area tags used by NJIT faculty.
Return the numbers of the tags that belong to the SAME research field as the query — a tag belongs if a
domain expert would file it under that field (subfields and near-synonyms count; a merely related but
DISTINCT field does NOT belong, e.g. "neural networks" does not belong under "computer networks").
FIELD: {area}
TAGS:
1. {tag}
2. {tag}
...
Answer with only the matching numbers, comma-separated. If none match, answer "none".
```
Temp 0. Parse integers defensively; ignore out-of-range/garbage.

## 4. Engineering decisions (owner-approved 2026-07-07)

- **① Live per-query + persistent cache.** Cache key = `(normalized_area, vocab_hash)` → verified tag set.
  First ask of a new topic pays one LLM call (~1s on a local model); every repeat is an instant, stable hit.
  Chosen over an offline-precomputed map because it handles any phrasing live and stays reproducible via cache.
- **② Enumerate skills only** (`people_by_research_area`, `count_people_by_research_area`). The per-person
  yes/no `does_person_research_area` stays **exact-match** — a fuzzy "yes, he does security" is worse than an
  honest tight answer there. `is_listed_research_area` / `people_by_area_tag` unchanged.
- **③ Fail-safe, not fail-loud.** LLM/embedding error, empty shortlist, or empty verify → fall back to today's
  exact-phrase match. This is a live user-facing bot: availability > completeness, and the fallback is exactly
  current behavior, so the change can **never be worse than status quo.** (Contrast the pilot's judge, which
  fails LOUD — that's a measurement instrument; this is production.)
- **④ Space/hyphen normalization** folded into candidate matching so `cyber security` / `cybersecurity` /
  `cyber-security` unify for free.

### 4.1 Open engineering choices for the reviewers / plan
- **Verify model (LLM-agnostic, config-driven).** Default gen model is `granite4:tiny-h`, which the
  processing-debt judge work found **weak at exactly this semantic-discrimination task**. Recommend the verify
  model be its own env knob (e.g. `AREA_VERIFY_MODEL`), default to a **capable** installed model (`llama3.1:8b`),
  and validate quality in the eval before trusting it. Reviewers: weigh model choice vs latency.
- **Cache storage.** Regenerable, non-precious. Options: a dedicated `area_expansion_cache` table (INSERT OR
  REPLACE, its own short-lived writable connection so the read-path skills stay write-free per the graph-write
  invariant) vs a JSON file with atomic write. Lean: table in the OPS DB (per split-ops) — survives restarts,
  multi-process-safe. Reviewers decide.
- **Vocab-embedding store.** Embed 1,379 tags once; cache to disk/table keyed by vocab hash; rebuild on miss.
  KNN in-process (small N). Confirm it doesn't need the sqlite-vec store (it can, but in-process is simpler here).

## 5. Components & interfaces

| Unit | Signature | Responsibility |
|---|---|---|
| `area_vocab(conn)` | `-> list[str]` | Distinct active area-tag values (dedup, display form). |
| `vocab_embeddings(conn)` | `-> (list[str], ndarray)` | Embed vocab once; cache by vocab hash. |
| `nearest_tags(area, k=30)` | `-> list[str]` | Embed query; cosine top-k over vocab. |
| `llm_verify(area, candidates)` | `-> list[str]` | LLM selects same-field subset; temp 0; defensive parse. |
| `expand_area_llm(conn, area)` | `-> set[str]` | Orchestrate + cache; `{}` on any failure (caller falls back). |
| hook in `people_by_research_area` / `_research_entities` | — | Union exact result with verified-tag people. |

Cache + vocab-embedding are the only new persistent state. Everything else is pure functions.

## 6. Testing (TDD)

- **Unit (stubbed LLM/embedder):** `llm_verify` returns the right subset from a canned candidate list and
  *rejects* a planted false friend; `expand_area_llm` returns `{}` and the caller falls back to exact when the
  LLM raises; cache hit skips the LLM; vocab-hash change invalidates cache.
- **Integration (real models, gated):** "cyber security" → ≥12 faculty **including** Neamtiu, Sharma, Shi, Yao,
  Zhang; "computer networks" → excludes `neural networks`/`social networks` people; "operating systems" →
  includes `distributed systems` people; a specific query ("recommender systems") is unharmed.
- **Regression:** every existing area/skills test still passes; `does_person_research_area` behavior byte-identical.
- **Eval:** add the verification questions to `eval/questions.txt` (per the grow-correctness-suite rule).

## 7. Honest tradeoffs / risks

- **Latency:** +~1s on the *first* ask of a research-area topic (cached after). Only research-area routes.
- **Verify-model quality is the real lever.** A weak model over/under-includes. Mitigated by: config-driven
  model + eval-validation gate before trusting; fail-safe fallback bounds the downside to status quo.
- **Non-determinism:** temp 0 + cache makes a given area's answer stable in practice; a cold cache after a vocab
  change re-derives. Acceptable for a chat bot (not a measurement instrument).
- **Not a data fix:** the fragmented/duplicate `ResearchArea` nodes remain; this makes retrieval robust to the
  fragmentation rather than removing it. De-dup is a separate, optional data-cleanup.

## 8. Goals checklist (shipped / deferred — per review-against-plan rule)

- [ ] Umbrella research query surfaces all owned field experts (cyber security 1→≥12) — **core, shipped by this**
- [ ] No hardcoded taxonomy; generalizes to any field — **core**
- [ ] Cannot invent people/areas (LLM constrained to owned tags) — **core**
- [ ] Enumerate list & count stay consistent — **core**
- [ ] Fail-safe fallback to current behavior — **core**
- [ ] Per-person yes/no stays exact — **core (unchanged)**
- [ ] Eval questions added — **core**
- [ ] `ResearchArea` node de-duplication — **DEFERRED (separate data-cleanup)**
- [ ] KG+KB winner-take-all merge for entity queries — **DEFERRED (separate, larger lever; parked)**
- [ ] Offline precomputed area map — **DEFERRED (chose live-cached)**

---

## 9. REVISION v2 — post-review (folds Fable RAG + senior-eng findings; both = approve-with-conditions)

Reviews: senior-eng (F1 blocking + F2–F7) and Fable RAG (findings 1–11). This section is authoritative where it
supersedes §3–§8. Every finding is resolved, committed, or explicitly deferred below.

### R1 — BLOCKING resolved — hook placement keeps the yes/no skill structurally exact
`_research_entities(conn, area, org_id)` gains a keyword `expand: bool = False`. When `expand=True` it returns
`exact_set ∪ {people who list any LLM-verified tag}`; when `False` it is byte-identical to today.
- `people_by_research_area` and `count_people_by_research_area` call with `expand=True` → they share ONE call
  shape → list==count stays **structural**, not by-convention.
- `does_person_research_area` keeps calling `expand=False` for its **membership/"yes"** verdict (decision ②
  preserved — no fuzzy yes).
Regression test asserts `does_person_research_area` "yes"/"unknown" verdicts are byte-identical to pre-change.

### R2 — BLOCKING resolved (Fable #2, the deep catch) — no false "no" after an expanded list
Today `does_person_research_area` returns a flat **"no"** when exact-match fails and the person lists *any* areas
(skills.py:353). Once the enumerate list is expanded, that yields a live contradiction: list says "…Neamtiu…",
follow-up "does Neamtiu work on cyber security?" says **"No"** — violating honest-partial's "never a false no."
**Fix (new `"related"` verdict state):**
1. `exact_in = entity_id ∈ _research_entities(expand=False)`. If true → `"yes"` (unchanged basis logic).
2. Else `related_in = entity_id ∈ _research_entities(expand=True)`. If true → verdict `"related"`, with
   `matched_area` = the person's verified sibling tag → wording: *"He lists **system security**, a form of
   security — I don't have 'cyber security' listed as such."* Honest-partial, basis-aware, NEVER contradicts
   the roster (nobody in the expanded list can get a flat "no").
3. Else → `"no"` if the person lists areas, else `"unknown"` (unchanged).
`does_person_research_area` docstring/contract updated: membership is no longer "identical" to
`people_by_research_area`; the guarantee becomes **"never contradicts the list"** (list ⊇ exact-yes; expanded-only
members answer `"related"`, never `"no"`). The renderer (`does_person_research_area` caller in structured_answer)
gains the `"related"` branch. This means the yes/no skill DOES consult the expansion for the no→related downgrade
(cached; same latency envelope) — a deliberate, explicit relaxation of decision ②'s wording, not a silent one.

### R3 — BLOCKING resolved (Fable #3) — complete cache key
Cache key = `(normalized_area, vocab_hash, verify_model_id, prompt_version, top_k)`. A model/prompt/K change
therefore never serves a stale verification. `prompt_version` is a constant bumped on any prompt edit.

### R4 — committed (Fable #4) — expansion is VISIBLE in the answer (anti-fabrication)
The rendered roster must not assert the bare umbrella attribute on a name whose tag is a sibling. Format as, e.g.:
`"15 faculty work in security-related areas: Chase Wu (cyber security), Iulian Neamtiu (system security), …"`
— each name annotated with the person's OWN verified tag. This (a) never attaches an unlisted attribute to a
name (honors CLAUDE.md), (b) makes any verify over-inclusion self-evident/checkable, (c) is free (the per-person
verified tag is already in hand). `structured_answer.format_answer` for these skills gains the per-name tag
annotation when the result came from the expanded path. **The §4③ safety claim is corrected**: not "never worse
than status quo" (over-inclusion is a precision risk status quo lacks) but **"never surfaces FEWER correct people,
and any over-inclusion is rendered transparently and gated by the precision eval (R7)."**

### R5 — committed (Fable #5) — candidate shortlist = KNN ∪ token-overlap (recall-only)
Candidates for the verify step = `nearest_tags(area, k=30)` **∪** `{owned tags sharing a non-stopword token with
the query}`. The token-overlap channel deterministically guarantees all `*security*` tags enter the candidate
set regardless of embedding rank — so the flagship case stops depending on embedding-rank luck. Zero precision
cost: the LLM still prunes. (This reuses §1's head-token analysis as a RECALL channel, never a decision channel —
the thing we explicitly rejected as a *decision* rule.) `top_k` stays 30 for the KNN arm.

### R6 — committed (senior F2, LLM-agnostic hard line) — the LLM seam is `generate_json_sync`
The verify step runs on the **synchronous** router path (`unified_router.decide`/`resolve_kg` — no event loop of
its own), so it uses the module-level **`generate_json_sync`** (ollama_client.py:21), NOT the async `generate`.
- Structured output: JSON schema `{"indices": [int]}` (constrained decoding; supersedes §3.1's comma-text parse).
- Model: a dedicated `AREA_VERIFY_MODEL` env knob (default **`llama3.1:8b`** — installed, verified; `granite4:tiny-h`
  rejected as too weak per the pilot's judge work), wired as its own `partial(generate_json_sync, model=…)`.
- Injected as a callable (mirror assistant.py:138–139 `gen_json`/`embedder` DI) → unit tests stub it, no Ollama.

### R7 — committed (both, Fable #7) — verify-model prompt + a real precision gate
- Prompt gains **2–3 few-shot examples incl. one DIRECTIONAL NEGATIVE** (a parent-field tag REJECTED under a
  *specific* query — e.g. query "recommender systems" must reject the tag "machine learning"). 8b models judge
  symmetric relatedness by default; the few-shot pins hyponym direction.
- **Gold-set gate (makes the LLM-agnostic rule operational):** a labeled set of ~50 `(query, tag, belongs?)`
  pairs mined from the real vocabulary, INCLUDING the measured traps (neural↔computer networks, service-learning,
  motor-learning, the `systems`-head fanout, distributed↔operating systems). Ship the flag ON only if the verify
  model scores **precision ≥ 0.9** on it; the gate must **re-pass on any `AREA_VERIFY_MODEL` swap.**
- Reject NLI-reuse (deberta-v3 is OOD on 2–4-word fragments) and self-consistency sampling (conflicts with
  determinism/cache).

### R8 — committed (Fable #8 + senior F5) — feature flag + observability
- **`AREA_EXPAND_ENABLED`** env flag (default per owner at build; matches `LIVE_ENABLED`/`ANSWER_GATE_ENABLED`
  precedent). Off → today's exact behavior; the one-line kill switch.
- **Structured logging** on every expansion: cache hit/miss, shortlist size (KNN/token arms), verify latency,
  verified-tag count, and a distinct **fallback reason** that separates "LLM said none" (legit; union=exact) from
  "LLM/embed ERRORED" (degraded — WARNING + a counter). Ops-facing; no user disclosure. Without this the fix
  degrades invisibly (fail-safe hides it).

### R9 — committed (senior F3) — cache + vocab-embedding storage
Both live in the **OPS DB** (`get_ops_connection`/`OPS_DB_PATH`, schema.py:30) on a **module-owned, short-lived
writable connection that commits itself** — NEVER the caller-owned `conn` (graph-write invariant). `INSERT OR
REPLACE` under WAL is multi-process-safe across the 3 bot processes. **JSON file rejected** (3 writers race).
Tables created idempotently via the `create_all()` pattern. `vocab_hash` is recomputed only when a cheap
change-detector fires (`MAX(rowid)`/`COUNT(*)` over research_areas items), with the vocab list memoized
in-process — so warm queries don't pay a full 1,379-row scan+hash each time.

### R10 — committed (senior F4) — Qwen asymmetric embedding, pinned
Query area → `Embedder.embed_query`; the 1,379 vocab tags → `Embedder.embed_document`; both L2-normalized (cosine
== dot). Do NOT use the private `_embed_batch` (no prefix, no norm). Vocab embedded once, batched, cached (R9).
Query-side canonicalization (Fable #10): run `expand_area`/`AREA_SYNONYMS` FIRST so "ml"/"hci" embed as
"machine learning"/"human computer interaction", not bare tokens.

### R11 — committed (senior F6/F7) — cold-load, timeout, DI, VRAM
`generate_json_sync` default `timeout=6.0`; `llama3.1:8b` is not resident → first post-restart verify may
model-load past 6s → silent fallback. Mitigate: raise the verify timeout (e.g. 20s) AND/OR Ollama `keep_alive`
pre-warm of `AREA_VERIFY_MODEL`. Document VRAM: granite (resident gen) + llama (verify) + Qwen embedder co-resident
on the 16GB box — confirm headroom at build. All new units take injected `embedder`/`verify` callables.

### R12 — builder's-discretion / explicitly noted
- Fable #6 (router loose-gate residual): loose-phrased umbrellas with **zero** literal tag match still fail the
  A15 `is_listed_research_area` gate (router.py:731) and route to RAG — the expansion never fires. **DEFERRED**
  (add to §8). Future one-liner: let the loose gate also accept a non-empty `expand_area_llm`. Not in scope now.
- Fable #11 (`_FACET_SUFFIX` ordering): the facet-strip retry fires on the **exact** result only (unchanged);
  expansion runs on the settled term. Pinned.
- Fable #9 nit: temp-0 is host-stable but not cross-quantization-stable → the **cache** is what delivers
  determinism (another reason R3's key completeness matters). Integration assertions ("≥12 incl. Neamtiu…") are
  **model-pinned/gated tests**, not CI regressions.

### R6 UPDATE (build-time deviation, recorded per review-against-plan): default verify model = gemma3:12b, chunked
The spec proposed `llama3.1:8b` as the default `AREA_VERIFY_MODEL`, to be validated by the gold gate. It was —
and it FAILED: llama3.1:8b scored **precision 0.65** (a yes-machine — even accepted "cyber security ← machine
learning"). Improved prompt lifted it only to 0.77. **gemma3:12b** (bakeoff-proven rejecter) with the strict v3
prompt scores **precision 1.0 / recall 1.0**. So the shipped default is **gemma3:12b** (the gate did exactly its
LLM-agnostic job — a swap was needed and the gate caught it). Two additions the build made beyond the spec:
(a) **chunked verify** (`AREA_VERIFY_CHUNK`, default 10) — a long candidate list dilutes even gemma into
over-including; chunking to 10 restored precision 1.0 live (folded into the cache key). (b) verify **timeout 30s**
(vs the proposed 20s) for gemma's ~17s cold-load. Ops note (Fable): gemma3:12b (8GB) + granite gen (4.2GB) +
qwen embed on the 16GB box is at the edge — verify a 3-model-resident VRAM check at deploy; the gate makes
re-gating to `llama3.1:8b` a safe fallback if it thrashes. Gold gate now scores the PRODUCTION (chunk-of-10)
shape, not just chunk-of-1.

### §8 checklist — corrected wording (per review-against-plan)
- "Surfaces all and only field experts" → **"surfaces substantially more correct experts (cyber security 1→≥12),
  recall-bounded by shortlist∪token-overlap and precision-bounded by the ≥0.9 gold gate; over-inclusion rendered
  transparently."**
- ADD core: **"Follow-up yes/no never contradicts the expanded list (no false 'no')"** (R2).
- ADD core: **"Expansion visible in answer wording; no unlisted attribute asserted on a name"** (R4).
- ADD core: **"Feature flag + degradation logging"** (R8).
- ADD deferred: **"Loose-phrased umbrellas with zero literal tag match still route to RAG"** (R12).

**Build gate:** R1–R3 (blocking) are resolved in this text; R4, R7, R8 committed to the plan. No code before R1/R2
land in the hook + yes/no contract (they set the module's public shape). Owner relaxed the sign-off gate
(2026-07-07): Fable's approve-with-conditions + these resolutions = go. Proceed: writing-plans → TDD build.
