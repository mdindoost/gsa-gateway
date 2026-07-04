# A3 — Context-rewrite antecedent-ambiguity guard

**Date:** 2026-07-04
**Roadmap item:** A3 (pipeline accuracy review, `03_ROADMAP.md`)
**Status:** design — awaiting Fable design-review + owner sign-off
**Design partner:** Fable (consulted 2026-07-04)

## Problem (execution-proven today)

`bot/core/context_rewrite.py` resolves a conversational follow-up into a standalone query
BEFORE routing/retrieval. Flow (`resolve_query`): deterministic referential gate `is_follow_up`
→ LLM `rewrite_with_context` → deterministic `verify_rewrite` guard → `(query, was_rewritten)`.

`verify_rewrite`'s entity-membership guard checks that every proper-noun the rewrite ADDED
appears **literally in the history** — but NOT that it is the **correct** antecedent. With
`FOLLOWUP_RESUME_ENABLED=1` (LIVE) a prior **roster** answer sits in history as text:

> assistant: "11 faculty work on brain imaging: Ana Rolim, Bharat Biswal, Bryan Pfister,
> Elisa Kallioniemi, Xin Di."

A bare singular pronoun follow-up then lets the LLM pick an ARBITRARY roster name and the guard
waves it through. Reproduced verbatim:

```
verify_rewrite("what is his h-index",
               "what is Bryan Pfister's h-index",
               "…11 faculty work on brain imaging: Ana Rolim, Bharat Biswal, Bryan Pfister, …")
  ->  "what is Bryan Pfister's h-index"        # PASSED — but which one is "his"? unknowable.
```

Downstream this becomes a **confident h-index answer for the wrong person** — a direct
violation of the never-fabricate / honest-partial hard line. The single-person case
("tell me about Guiling Wang" → "what is his h-index") is the INTENDED good path and MUST keep
resolving.

Secondary: `_added_entities` requires `t[0].isupper()`, so an LLM emitting a **lowercase**
hallucinated name bypasses the entity-membership check entirely.

## Goals

1. **G1 — No confident wrong-person answer** from a roster-in-history + singular-pronoun
   follow-up. The genuinely-ambiguous case must never produce a resolved query naming one
   arbitrary roster member.
2. **G2 — The good single-person follow-up keeps working** (the one unforgivable regression).
3. **G3 — Better UX than a bare deflect** when we hold the names: ask *which one?* rather than
   silently dropping the resolve.
4. **G4 — Fail toward passthrough**, never toward a novel answer; never break the message path.

Non-goals (loudly deferred): pending-action re-execution of the clarify reply (v1.5); a
targeted lowercase-name fix (deferred with a measurement log — see §Deferred).

## Design — two complementary layers

### Layer 1 (primary): tag-at-source + pre-LLM ambiguity gate → **clarify**

**Why not "count people in free text":** undecidable at the fidelity we need. "Bryan Pfister"
and "Computer Vision" are textually identical (two capitalized tokens); a regex person-counter
either inflates on capitalized area/org lists (suppressing the good single-person case — the one
regression we must not ship) or misses real people. Do not build a person-NER out of regex.

**Tag the source instead.** The structured layer already KNOWS the person set of every answer
(it has the rows). Carry that knowledge on the turn:

- `ConversationTurn` gains `person_names: list[str] = field(default_factory=list)` — precedent:
  it already carries `source_files`.
- `ConversationManager.add_turn(...)` gains an optional `person_names: list[str] | None = None`
  (default → `[]`; backward compatible). `get_history` adds `"person_names"` to each dict
  (every consumer reads via `.get`, backward compatible).
- New single-source helper `structured_answer.person_names_of(result: dict) -> list[str]` — a
  per-skill extractor registry (rows are heterogeneous: `(name,title,email)` tuples,
  `h["name"]` dicts, `(name, areas)` pairs, `candidates[].name`). Covers EVERY person-bearing
  skill, single-person ones included (uniform evidence: `entity_card`/`metric_of_person` tag
  their 1 name). **The registry default for an UNKNOWN skill is `[]`, never `rows`.** Enumerated
  traps (Fable review):
  - `count_people_by_research_area` — `rows` is an **int** (`structured_answer.py:106`): a naive
    row-iterator crashes → map to `[]`.
  - `areas_in_org` / `area_counts` / `org_departments` / `orgs_by_type` — rows are org/area
    **strings**, not people → `[]` (else "his" after "what areas are in YWCC" clarifies with
    research areas as "people").
  - `org_disambig` candidates are shape-identical to `person_disambig` (`c["name"]`) but are
    ORGS → `[]`.
  - `entity_card` can LACK a name: `run()` uses `a.get("name")` (:48); the person-disambig
    fallback resume builds `Route("entity_card", {"entity_id": …})` with no name
    (`structured_answer.py:544`) — precisely the roster→disambig→pick→"his h-index" flow where
    the tag matters most. **Fix at the source: add `"name": c["name"]` at :544** so the card
    always carries its name.
  - Tags reflect the ROW SET, not the rendered text (`top_people_by_metric` / the >25
    `people_by_role` branch display fewer names than the rows hold) — this errs toward clarify,
    which is safe.
- Both roster-writing chokepoints tag the assistant turn. **The result dict dies inside the
  worker thread**, so names are computed where the rows exist and threaded out:
  - `_try_structured` path — `person_names_of(result)` is computed inside `_run`/`_structured`
    (where the dict lives) and added to the thread-boundary tuple
    (`message_handler.py:573-574`); `_register_and_record` (:609-631) grows a `person_names`
    param and tags the assistant turn with it.
  - v2.1 path — `_structured_from_route` (:661-666) computes names into its tuple;
    `_answer_decision` (:710-715) passes them to `_register_and_record`.
  - resume path — `_resume_pending` (:633-647) computes names and returns them alongside the
    text so the tag at :333-337 carries them (the `PendingOption.payload` alone has no rows).

**The gate runs PRE-LLM in `resolve_query`:** if the message contains a bare **singular personal
pronoun** (his/her/hers/him/he/she) AND the **immediately-preceding assistant turn** carries
**≥2** tagged names → do NOT call the LLM → return a **clarify** signal listing those names.

- **Adjacency-scoped, NOT most-recent-tagged, NOT union (Fable Hole C).** Tags exist only on
  structured answers, so "most-recent *tagged* turn" goes stale across an untagged RAG/live turn:
  roster (tagged, 11) → "who is the dean of students?" (RAG, untagged) → "his email" would
  wrongly clarify with 11 stale names. Keying on the *immediately-preceding* assistant turn ties
  the pronoun to what was literally just said. And a union would break narrowing (roster → "tell
  me about Bryan Pfister" → "his h-index" must resolve). This still catches the motivating bug
  (roster is the immediately-preceding turn there).
- Acknowledged v1 limitation (fails toward a question, not a wrong answer): a message that itself
  names the person but still has a pronoun ("Bryan Pfister's h-index and his email", post-roster)
  clarifies unnecessarily. Acceptable.
- Clarify text: `"You mentioned several people — which one did you mean? Ana Rolim, Bharat
  Biswal, Bryan Pfister, Elisa Kallioniemi, Xin Di (or give the full name)."` Cap at ~5 names
  + "or the full name".

### Layer 2 (backstop): picked-name-is-a-list-item check in `verify_rewrite` → **passthrough**

Covers rosters that layer 1 can't tag — a RAG **prose** answer that happens to name several
people (untagged), or a future roster-render change. The precise ambiguity condition is NOT
"many persons in history"; it is "the LLM's picked antecedent was plucked from a list of
siblings." So check the picked name locally (against the **original-case** history — the backstop
must NOT use the lowercased `hist` at `context_rewrite.py:83`, it needs capitalization):

- After the existing entity-membership loop, if the rewrite added **exactly one** name, scan its
  occurrences in history. Classify each occurrence as a **list-item** iff the name sits inside a
  genuine list — a **chain of ≥3 comma/`and`-separated capitalized runs** (`A, B, C` / `A, B and
  C` / newline-bullet siblings). Block → passthrough ONLY if the name is a list-item in an
  occurrence AND has **no non-list occurrence** (Fable Hole A: accept if ANY occurrence is
  non-list — the good "tell me about Bryan Pfister" turn creates a non-list occurrence that
  rescues the later "his h-index").
- **Appositive guard (Fable Hole B — the R1 trap).** The `Name, Title` NJIT pattern ("Bharat
  Biswal, Distinguished Professor of Biomedical Engineering, directs…") must NOT count as a list
  — that's why a single comma-flank is insufficient. Requiring a **≥3 capitalized-run chain**
  (not a bare `, Capitalized`) excludes the two-run appositive. A `Name, Title` pair is 2 runs →
  not a list → accepted.
- "Guiling Wang researches Computer Vision, Machine Learning" → "Guiling Wang" is followed by
  lowercase prose, no chain → single antecedent → accepted.
- "…: Ana Rolim, Bharat Biswal, Bryan Pfister, Elisa Kallioniemi, Xin Di." → "Bryan Pfister"
  sits in a ≥3 chain → list-item; no non-list occurrence → blocked.
- **Number-blind on purpose.** Block when the rewrite added exactly ONE list-member name; ALLOW
  when it added ≥2 (a legitimate resolve-to-set for "their emails"). This catches singular-"they"
  ("what is their h-index" → LLM singularizes to one roster name) at the exact moment it becomes
  dangerous, without blocking plural resolution.
- Two people in **separate turns/sentences with no list** are deliberately ALLOWED — that's
  genuine anaphora with a recency prior, normal conversation, not fabrication. The pathology is
  exactly the 1-of-N-from-a-roster pick; the flank check targets exactly that.
- Acknowledged limitation (layer 1 covers it, so note-don't-build): the structured skills'
  `"Title — Name (email); …"` **semicolon** rosters (`officers_in_org`, `role_in_org`,
  `people_in_org`, `people_by_role`) are NOT comma/`and` chains → the backstop alone would miss
  them. Fine because those are tagged (layer 1); the backstop is the untagged-prose catch-all,
  not roster-complete.

## Clarify vs passthrough — split by evidence quality

- **Layer 1 (tagged, exact names) → CLARIFY.** We hold the names; a passthrough here produces the
  guaranteed-bad UX the rewrite feature exists to kill (list 11 names, user asks "his h-index",
  bot deflects). `MessageResponse(is_abstain=True, abstain_reason="ambiguous-antecedent")` already
  exists. `resolve_query`'s return grows from `(query, bool)` to a small `RewriteResult(query,
  rewritten, clarify_text)`; the single call site (`message_handler.py:350`) short-circuits to
  return the clarify response when `clarify_text` is set.
- **Layer 2 (backstop, no trustworthy list) → PASSTHROUGH.** We caught a suspicious pick but
  regex-scraped names would themselves be a fabrication risk to enumerate. Passthrough →
  downstream deflect is the honest floor.

**v1 ships clarify WITHOUT a PendingAction** — the user re-asks with a name; that routes normally.
**v1.5 (deferred):** register a `requery` PendingAction so the bare reply "Bryan Pfister"
deterministically re-executes (needs a new pending action kind — today's pending only carries
`"structured"` skill payloads). Separable; must not block v1.

## isupper / lowercase bypass — KEEP isupper; defer with a measurement log

A blanket drop of `isupper()` in `_added_entities` makes every synonym/glue word the LLM adds a
required history-member → legit rephrases fail the entity-membership check → passthrough-on-good-
rewrites re-opens the #1-evidenced bug. So the naive fix is wrong.

Residual exposure after this design is tiny: the pre-LLM tag gate fires BEFORE the LLM emits
anything in the ≥2 case, so the lowercase bypass only matters when history has a **single** person
AND the LLM both lowercases AND hallucinates a different name on a one-antecedent task — rare².
**Defer, but instrument:** the accepted-rewrite INFO log (`context_rewrite.py:144`) also logs
added-but-unverified lowercase content words (in resolved, not in original, not `_STOP`, not
capitalized) so real-world incidence is measured before we engineer for it.

## Pronoun-number rule

- **Pre-LLM gate: singular personal pronouns only** — his/her/hers/him/he/she. Plural
  (their/them/they) over a roster has a valid antecedent (the set); blocking it would break
  "what are their emails".
- **Never gender-narrow.** A roster with one male-sounding name does NOT make "his" unambiguous —
  gender inference is fabrication-adjacent. ≥2 persons is ≥2 candidates, full stop.
- `its`, demonstratives, and elliptical openers ("what about CS?") stay OUTSIDE both new checks —
  org-antecedent and topic-ellipsis behavior is untouched.

## Blast radius

The design acts ONLY when (bare singular personal pronoun) AND (≥2 tagged persons on the
immediately-preceding assistant turn, layer 1) OR (picked name is a ≥3-run list-chain member with
no standalone occurrence, layer 2).
Everything else — single-person follow-ups, openers, demonstratives, `its`, free mode, no-history
— takes today's exact path. Both layers fail toward passthrough, never toward a novel answer.

## Flag

`ANTECEDENT_GUARD_ENABLED` (default off) gates BOTH layers. Flag off = **zero behavior change,
pinned by a byte-identical flag-off test** (gate/backstop no-op, `resolve_query` returns the old
resolution). The `person_names` tags are ALWAYS written on turns regardless of the flag — they are
inert when off (no consumer reads them; `_format_history` and `format_history_for_prompt` ignore
the extra key), which keeps the write path flag-free. `resolve_query` returns a stable
`RewriteResult` shape in BOTH flag states (no type-unstable tuple-vs-object switch); flag-off simply
always leaves `clarify_text=None`, and the single call site reads `.query`/`.clarify_text`. Owner
flips after review, mirroring the A15b flow.

**Acknowledged layer-2 limitations (under-block only — fail toward passthrough, never a wrong
answer):** (a) semicolon rosters (`"Title — Name; …"`) aren't comma/`and` chains → not detected by
the backstop (layer 1 tags those); (b) a rewrite that MERGES the name with an adjacent capitalized
word ("Bryan Pfister H-index", "Professor Bryan Pfister") forms one run ≠ the bare name → not
blocked; (c) newline-bullet rosters without a comma/`and` between items aren't detected. All three
are safe (the wrong-person answer is only produced by the 1-of-N comma/`and` pick, which IS caught);
noted so nobody mistakes the backstop for roster-complete.

## Risks & mitigations

- **R1 — regress the good single-person rewrite (unforgivable).** Backstop only blocks when the
  picked name is a member of a ≥3-run list-chain with NO standalone (non-list) occurrence AND is
  the only added name (excludes `Name, Title` appositives and rescues a later standalone
  mention); golden tests include
  single-person-prose-with-capitalized-area-lists and "Guiling Wang → his h-index" verbatim;
  every suppression logs its reason; cases added to `eval/questions.txt`.
- **R2 — tag pipeline drift** (a future multi-person path forgets to tag → gate blind). Tag at the
  two chokepoints only, never per-skill; names derive from the single `person_names_of()`; a unit
  test asserts every people-listing skill family yields names through it; layer-2 backstop remains
  the untagged catch-all.
- **R3 — clarify/pending interaction.** Clarify inherits the existing one-shot pending semantics
  (cleared next turn; unmatched reply falls through to normal routing — the machinery at
  `message_handler.py:343` already does this). v1 ships clarify-without-pending, so the only new
  moving part is the `MessageResponse` short-circuit.
- **Non-risk (record):** the `[:500]` history-text truncation can cut a roster mid-list in TEXT,
  but tags carry the full name set — the gate judges the answer's true referent set, not the
  surviving text. Another reason tags beat text as primary.

## Test plan (TDD)

- `verify_rewrite` backstop: roster ≥3-chain picked-one → passthrough; single-person-prose +
  capitalized area list → accepted (R1 witness); **`Name, Title` appositive → accepted** (R1
  Hole-B witness); **cross-layer: roster turn THEN "tell me about Bryan Pfister" turn (non-list
  occurrence) THEN "his h-index" → accepted** (R1 Hole-A witness); resolve-to-set (≥2 added) →
  accepted; singular-"they" singularized-to-one → passthrough.
- `person_names_of`: one witness per person-bearing skill family (roster tuples, dict rows,
  `(name,areas)`, candidates, single-person card) → correct name list; **`count_*` (int rows) →
  `[]` no crash; `areas_in_org`/`orgs_by_type`/`org_disambig` (strings/orgs) → `[]`; unknown
  skill → `[]`**.
- Pre-LLM gate: ≥2 tagged on the **immediately-preceding** assistant turn + "his …" → clarify
  (LLM NOT called); single tagged + "his …" → normal LLM rewrite; **stale-tag (roster tagged,
  then untagged RAG turn, then "his …") → NOT clarified** (Hole-C witness); recency (roster →
  1-person turn → "his") → resolves, not blocked; plural "their …" over roster → not blocked.
- Flag off → byte-identical old behavior (tags `[]`, no clarify, `RewriteResult.clarify_text`
  always None, `.query` == old resolved).

## Goals checklist (shipped/deferred)

- G1 no wrong-person answer — **shipped** (layers 1+2).
- G2 single-person keeps working — **shipped** (recency-scope + flank-only backstop + tests).
- G3 clarify UX — **shipped** (layer-1 tagged path).
- G4 fail-to-passthrough — **shipped** (both layers).
- Lowercase-name targeted fix — **deferred** (measurement log shipped).
- Clarify-reply re-execution (requery pending) — **deferred to v1.5**.
