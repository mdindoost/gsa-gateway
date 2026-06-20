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

### Facet A — guard surname resolution (`router.py` `_resolve_surname`)
Two cheap, layered guards (belt + suspenders):
1. **Length guard:** only attempt the surname fallback when the (prefix-stripped) query is short — ≤ 4
   content tokens (`_qtokens`). A 15-word meta sentence never triggers a bare-surname lookup. Short
   queries ("oria", "professor wang", "oria email", "koutis info") still work.
2. **Common-word stoplist:** skip surname tokens that are common English words (a curated set:
   see, may, will, can, best, young, long, white, brown, black, green, gray, day, week, may, ...). Applied
   in `_resolve_surname`, so "see" never resolves to "Adam See" from a stray "I see". (Adam See is still
   reachable by full name "Adam See" via `persons_in_query`; only the bare common-word surname is blocked.)

Both guards together: the length guard kills the long-sentence class structurally; the stoplist kills the
short-ambiguous class. Net effect: no wrong-person answers from incidental words.

### Facet B — profile-link queries (registry-driven, mirrors metric_of_person)
- **Registry** (`profile_fields.py`): give each `Field` an `aliases` tuple and add
  `match_link_field(text) -> (field_key, Field) | None` (word-boundary, like `match_metric`). Aliases:
  scholar←("google scholar","scholar","gscholar"); linkedin←("linkedin","linked in"); github←("github");
  orcid←("orcid"); website←("website","homepage","home page","web page","webpage","personal site/page").
- **Router branch** (after the metric branch, before generic person branches): if `match_link_field(q)`
  AND a person resolves (reuse `_resolve_person`) → `Route("link_of_person", {entity_id, name, field_key})`.
  Ambiguous surname → `person_disambig` (as elsewhere). No person → fall through (don't invent).
- **Skill** (`entity.link_of_person(conn, entity_id, field_key) -> {name, field_key, url}`): read the URL
  from `attrs.profiles[field_key]` via the registry's existing `_field_url` (honors the website fallback);
  honest-empty when absent.
- **Render** (`structured_answer`): deterministic, no LLM (numbers/URLs never reworded — same as metrics):
  - has it: "Vincent Oria's LinkedIn: https://www.linkedin.com/in/vincent-oria-7b06a114"
  - honest-empty: "I don't have a LinkedIn on file for Vincent Oria." `is_deterministic` → skip compose.

**"X scholar" disambiguation:** `link_of_person` returns the Scholar **profile URL** (the link). Scholar
**metrics** stay on "X research / X citations" (the existing metric routing). They don't conflict — "scholar"
asks for the page, "citations/research" asks for the numbers.

## Open decisions (for review/Mohammad)
- **D1** — length-guard threshold (≤4 content tokens). Reasonable? (Short person queries still pass.)
- **D2** — `link_of_person` as a focused deterministic answer (recommended; consistent with metrics) vs
  just routing link-words to `entity_card` (shows the whole card + all links). Rec: focused.
- **D3** — common-word stoplist contents (start small + curated; expand as needed). Accept a curated list?

## Goals checklist (completeness — `[[feedback_review_against_plan]]`)
- [ ] G1 Length guard on `_resolve_surname` (long meta sentence → no surname lookup).
- [ ] G2 Common-word stoplist in `_resolve_surname` ("see" etc. never resolve as a surname).
- [ ] G3 `match_link_field` + `Field.aliases` in the registry.
- [ ] G4 Router `link_of_person` route (link word + resolvable person; disambig/fall-through correct).
- [ ] G5 `entity.link_of_person` + deterministic render (focused link / honest-empty), `is_deterministic`.
- [ ] G6 Existing behavior preserved: "oria email"/"vincent oria"/"koutis info"/"who is X" still route as before.

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
