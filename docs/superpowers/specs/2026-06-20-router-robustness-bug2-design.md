# Router robustness (Bug 2) — stop wrong-person matches + route profile-link queries

> **Status:** Design — awaiting senior-eng + RAG review (incl. completeness-vs-plan) → Mohammad approves
> → TDD. **Date:** 2026-06-20 · with Mohammad Dindoost.
> **Related:** `v2/core/retrieval/router.py` (`_resolve_surname`, `_INFO_CUE`, `_PERSON_ATTR`),
> `v2/core/retrieval/entity.py` (`persons_by_lastname`, `person_attrs`), `v2/core/people/profile_fields.py`
> (link registry), the metric-queries pattern (`2026-06-19-metric-queries-design.md` — this mirrors it),
> `[[feedback_review_against_plan]]`.

## Problem (two confirmed facets of one root cause: the rule-based router is too narrow/eager on free text)

**A. Wrong-person over-firing.** The meta message *"I **see** you used he for Vincent… **everything**
should be Them…"* answered about **Adam See** — a different person. Confirmed: `_INFO_CUE` fires on
**"everything"**, then `_resolve_surname` iterates every token and **"see"** resolves to the surname **See**
(Adam See) → his entity_card. Two common English words (one an info-cue, one a real surname) hijacked a
long meta sentence into a confident wrong-person answer.

**B. Profile-link queries deflect.** `"oria linkedin"` and `"oria scholar"` → **route to None → RAG →
"I didn't find it"**, even though Oria's profile stores `linkedin` and `scholar` URLs. Confirmed:
`"vincent oria"`→entity_card and `"oria email"`→entity_card (email is a recognized attribute), but the
router does **not** recognize link words (linkedin/scholar/github/orcid/website) as person attributes, so
the focused link question never reaches the data. (Ironically the *general* "vincent oria" surfaces the
links; the *specific* "oria linkedin" fails.)

## Goal
1. The router must **not** resolve a person from a long, sentence-like message or from a common English
   word that merely happens to be a surname.
2. `"<person> linkedin / scholar / github / orcid / website"` must return that person's stored link
   (focused, deterministic) — or an honest "I don't have that on file."

## Design

### Facet A — guard surname resolution (`router.py` `_resolve_surname`) — REVISED per review
**Length guard ONLY. The stoplist is DROPPED** (senior + RAG review, BLOCKER): a static common-word
denylist breaks REAL faculty — the live directory has surnames **Young (×4), White, Brown** (+ likely
Long, Green, May) — and it can't even include "see" safely since **Adam See is a real person**. So:
- **Length guard, inside `_resolve_surname` (at the top, before the per-token loop)** so ALL three callers
  inherit it (metric branch + research branch via `_resolve_person`, and the entity-card surname branch).
  Only attempt surname resolution when the **prefix-stripped** query (`_NAME_PREFIX.sub("", q)`, the same
  string the function already tokenizes) has **≤ 4 content tokens** (`_qtokens`). The Adam See meta message
  is 11 content tokens → blocked; legit short queries ("oria"=1, "professor wang"=1, "oria email"=2,
  "koutis info"=2, "who is wang"=3) pass.
- **No stoplist.** Adam See remains reachable by full name ("Adam See", via `persons_in_query`).
- Accepted residual: a rare *short* phrase that pairs a cue with an incidental common-word surname
  (e.g. "see profile") could still resolve — unfixable via denylist (Adam See is real) and low-risk.
- Guarding stays in the **router** (`_resolve_surname`), NOT in `entity.persons_by_lastname` (that feeds
  disambiguation and must stay a pure data lookup — e.g. "professor young" must still return all 4 Youngs).

### Facet B — profile-link queries (registry-driven, mirrors metric_of_person)
- **Registry** (`profile_fields.py`): give each `Field` an `aliases` tuple and add
  `match_link_field(text) -> (field_key, Field) | None` (word-boundary, like `match_metric`). Aliases:
  scholar←("google scholar","scholar","gscholar"); linkedin←("linkedin","linked in"); github←("github");
  orcid←("orcid"); website←("website","homepage","home page","web page","webpage","personal site/page").
- **Router branch** (after the metric branch, before generic person branches): if `match_link_field(q)`
  AND a person resolves (reuse `_resolve_person`) → `Route("link_of_person", {entity_id, name, field_key})`.
  Ambiguous surname → `person_disambig` (as elsewhere). No person → fall through (don't invent).
- **Skill** (`entity.link_of_person(conn, entity_id, field_key) -> {name, field_label, url}`): read the URL
  via the registry's existing `_field_url(attrs, field_obj)` (honors the website `attrs_fallback`); use the
  registry `Field.label` for display casing ("LinkedIn"). honest-empty (url=None) when absent.
- **Render** (`structured_answer`): deterministic, no LLM (URLs never reworded — same as metrics):
  - has it: "Vincent Oria's LinkedIn: https://www.linkedin.com/in/vincent-oria-7b06a114"
  - honest-empty: "I don't have a LinkedIn on file for Vincent Oria."
  - **WIRINGS (review must-dos):** (1) add `"link_of_person"` to `_DETERMINISTIC_SKILLS`; (2) the
    honest-empty render is **TERMINAL** — it returns the line, NOT `""` (which would fall to RAG and risk
    surfacing a stale/hallucinated link, unlike the entity-layer "empty→RAG" convention); (3) `match_link_field`
    compiles longest-alias-first (like `_METRIC_MATCHERS`); (4) the link branch FALLS THROUGH (no early
    `return`) when no person resolves, so "what's on the GSA website" / "how do I use github" go to RAG.

**"X scholar" disambiguation:** `link_of_person` returns the Scholar **profile URL** (the link). Scholar
**metrics** stay on "X research / X citations" (the existing metric routing). They don't conflict — "scholar"
asks for the page, "citations/research" asks for the numbers.

## Open decisions (for review/Mohammad)
- **D1** — length-guard threshold (≤4 content tokens). Reasonable? (Short person queries still pass.)
- **D2** — `link_of_person` as a focused deterministic answer (recommended; consistent with metrics) vs
  just routing link-words to `entity_card` (shows the whole card + all links). Rec: focused.
- **D3** — common-word stoplist contents (start small + curated; expand as needed). Accept a curated list?

## Longer-term direction (note, not built now)
This is the 3rd router over/under-fire on sentence-shaped free text (FYA regex, Adam See, this). Per-bug
guards are accreting. Eventual target (record, defer): a single uniform "is this sentence-shaped free
text?" front-gate that suppresses incidental-token/bare-surname resolution everywhere, and ultimately a
*constrained* LLM intent/slot-gate (person? attribute? metric/link?) with a confidence floor used only to
GATE the deterministic skills — rendering stays deterministic, so anti-fabrication is preserved. Near-term,
per-bug guarding remains the right call (8B is unreliable at orchestration).

## Goals checklist (completeness — `[[feedback_review_against_plan]]`)
- [ ] G1 Length guard INSIDE `_resolve_surname` (prefix-stripped ≤4 content tokens) — all 3 callers inherit it.
- [ ] G2 ~~Common-word stoplist~~ **DROPPED** (breaks real surnames Young/White/Brown; length guard suffices).
- [ ] G3 `match_link_field` + `Field.aliases` (longest-alias-first, word-boundary).
- [ ] G4 Router `link_of_person` route (link word + resolvable person; disambig; fall-through on no person).
- [ ] G5 `entity.link_of_person` + deterministic render: in `_DETERMINISTIC_SKILLS`; honest-empty TERMINAL (not →RAG); reuse `_field_url`.
- [ ] G6 Existing preserved: "oria email"/"vincent oria"/"koutis info"/"who is X" AND "professor Young"/"who is White" still resolve.

## Testing (TDD)
- `_resolve_surname`/router: the Adam See meta message → NOT entity_card (None/RAG); "see"/"everything"
  guarded; short queries still resolve ("oria"→card, "professor wang"→disambig/card). Stoplist unit test.
- `match_link_field`: each alias hits the right field; non-link text → None; "google scholar"→scholar.
- router: "oria linkedin"→link_of_person(linkedin); "oria scholar"→link_of_person(scholar); "oria github"
  (no github on file → still routes, honest-empty at render); ambiguous surname + link → disambig;
  "linkedin" alone (no person) → fall through.
- `entity.link_of_person` + render: has-link, honest-empty, website fallback; deterministic (no compose).
- regression: metric routing ("oria citations") and "oria email"/"vincent oria" unaffected.
- eval: add "oria linkedin", "Koutis scholar", and the Adam-See-style meta negative to `eval/questions.txt`.

## Files touched
- `v2/core/people/profile_fields.py` — `Field.aliases`, `match_link_field`.
- `v2/core/retrieval/router.py` — surname length guard + stoplist; `link_of_person` branch.
- `v2/core/retrieval/entity.py` — `link_of_person`.
- `v2/core/retrieval/structured_answer.py` — wire `link_of_person` (deterministic).
- `v2/tests/`, `eval/questions.txt`. No schema change; code change → restart.
