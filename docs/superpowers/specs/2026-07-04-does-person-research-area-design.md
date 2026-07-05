# Design — `does_person_research_area`: person-scoped yes/no for "is he working on ML?"

**Date:** 2026-07-04 · **Author:** Claude (Opus) · **Status:** REV 2 (post 3-reviewer loop) → owner approval
**Fixes:** Gap #1 of the "is he" follow-up bug ([[project_person_area_yesno_bug]]).
**Reviews folded in:** Fable (APPROVE-WITH-CHANGES: R1,R2) · senior-eng (CHANGES-REQUIRED: B1,B2,N1–N5,
verified consistency invariant against live DB) · RAG-researcher (CHANGES-REQUIRED: basis-aware wording).
All three verified the **core mechanism is correct**; every change below is router-wiring or answer-wording.

**Owner-confirmed live evidence (2026-07-04 19:05):** `who is koutis` → correct card; `is he working on
machine learning?` → resolves "he"→Koutis (✅ core bug fixed) but answers with the **whole 42-name ML
roster** instead of a per-person yes/no. This design fixes the SHAPE.

---

## 1. Problem
Router sees a query with **both a resolved person AND a research area** ("is Koutis working on machine
learning?") and hits `router.py:658 → people_by_research_area(area)`, which **drops the person** and
returns everyone in that area. A one-person yes/no becomes a population dump.

## 2. Goal
Route person + area → a person-scoped yes/no that is **provably consistent with the population skill**,
renders **deterministically** (no LLM reword), degrades **honest-partial** when the person lists no areas,
and **never over-claims** (a prose-only match must not say the person "lists" the area). Everything else
(population "who works on X", org-scoped, faculty enumeration) is unchanged.

## 3. Core design decision — reuse the population membership set  *(verified correct, all 3 reviewers)*
`skills._research_entities(conn, area, org_id)` → the exact `set[entity_id]` the population skill uses.
**The yes/no is: `entity_id ∈ _research_entities(conn, area, org_id=None)`.**
- **Consistency guarantee** — same query filtered to one id, so the yes/no can NEVER contradict
  `people_by_research_area(area)`. senior-eng verified both directions hold against the live DB
  (`_named_rows` never drops an id; identical `is_active=1` filtering). No synonym table, no drift.
  Satisfies [[feedback_no_bandaid_align_data_and_retrieval]].
- `org_id=None` on purpose — "is Koutis in the ML set **anywhere**", not restricted to home org.

**The prose/tag asymmetry (drives R2/B2 wording below):** `_research_entities` FTS-matches over
`_RESEARCH_TYPES = (research_areas, research_statement, overview)` — i.e. area TAGS **and** profile
PROSE. `research_of_person(entity_id)["areas"]` reads only discrete **tags + `researches` edges**. So a
"yes" can come from prose while the tag list omits the area. The membership stays the source of truth for
yes/no; the **wording must reflect which evidence fired** (§4 `basis`, §6 templates).

## 4. The skill (`v2/core/retrieval/skills.py`)
```python
def does_person_research_area(conn, entity_id: str, area: str, name: str | None = None) -> dict:
    """Yes/no: does ONE person research ``area``? Membership is IDENTICAL to people_by_research_area
    (entity_id ∈ _research_entities), so the two can never disagree. `basis` records whether the hit
    is a discrete area TAG or profile PROSE, so the renderer never over-claims a 'listed' area.
    Honest-partial ('unknown') when the person lists no areas at all."""
    in_set = entity_id in _research_entities(conn, area, org_id=None)
    prof   = entity.research_of_person(conn, entity_id)         # {name, areas, statement}
    # tag-level confirmation: does the asked area (or a synonym) word-boundary-match a LISTED tag?
    pats = [re.compile(r"\b" + re.escape(t.casefold()) + r"\b")
            for t in expand_area(area) if (t or "").strip()]
    tag_match = any(p.search(pa.casefold()) for pa in prof["areas"] for p in pats)
    return {
        "entity_id": entity_id,
        "name": name or prof["name"],
        "area": area,
        "answer": "yes" if in_set else ("no" if prof["areas"] else "unknown"),
        "basis": "tag" if (in_set and tag_match) else ("prose" if in_set else None),
        "person_areas": prof["areas"],
    }
```
`answer`: `"yes"` (in set) · `"no"` (not in set, HAS listed areas) · `"unknown"` (no listed areas →
honest-partial). `in_set` is checked FIRST, so "in set via prose but empty tags" resolves to **yes**,
never a contradictory "unknown" (senior-eng verified ordering). Uses the same `expand_area` synonyms the
population validator uses — no new match semantics.

## 5. Router branch — insert EXACTLY between line 657 (`how many`) and line 658 (`if area:`)  *(N4)*
So the `how many` count branch keeps precedence and the population `if area:` is the fall-through.
```python
# person + area → per-person yes/no ("is Koutis working on ML?"). MUST precede the population
# `if area:` branch, which would otherwise drop the person and dump the whole area roster.
if area and (named or _PERSON_AREA_PRED.search(q)):
    person = _resolve_person_scoped(conn, q_for_area, area, named)   # B1: guard-bypassing resolver
    if isinstance(person, Route):                    # ≥2 surname/full-name matches → disambiguate,
        return _with_origin(person, "does_person_research_area", {"area": area})  # resume re-runs this
    if isinstance(person, dict):
        return Route("does_person_research_area",
                     {"entity_id": person["entity_id"], "name": person["name"], "area": area})
    # person cue but nobody resolved → fall through to the population branch (unchanged)
```

### 5a. B1 (CRITICAL) — resolve the person on the SUBJECT SPAN, bypassing the >4-token guard
**Why the naive design was a no-op:** `_resolve_person` → `_resolve_surname` hard-returns `None` for any
query with >4 content tokens (`router.py:554`, a guard against long meta-sentences being surname-mined).
"is koutis working on machine learning" = 6 tokens → `None` → falls through to the 42-roster. senior-eng
**proved this against the live DB** (`named=[]`, `_resolve_person=None`). So the predicate arm was inert.
The >4 guard is correct for *free-form* queries but must not apply to a **structurally isolated subject**.

New resolver (in `router.py`), operating on the org-stripped `q_for_area`:
```python
_AUX_LEAD = re.compile(r"^\s*(?:is|are|does|do|did|has|have|was|were)\s+", re.I)

def _resolve_person_scoped(conn, q_for_area, area, named):
    # Full name in the query wins (unambiguous), same as _resolve_person's first arm.
    if len(named) == 1: return {"entity_id": named[0]["entity_id"], "name": named[0]["name"]}
    if len(named) > 1:  return Route("person_disambig", {"candidates": named})
    # Else isolate the SUBJECT: drop from the area-introducing verb onward, then the leading auxiliary.
    m = _AREA_TRIGGER.search(q_for_area)
    subject = (q_for_area[:m.start()] if m else q_for_area.replace(area, " "))
    subject = _AUX_LEAD.sub("", subject).strip()          # "is koutis (currently)" -> "koutis (currently)"
    # Resolve surnames on the SHORT subject span directly — NO >4 guard (the span is already isolated).
    hits = []
    for tok in _qtokens(subject):
        cands = entity.persons_by_lastname(conn, tok)
        if len(cands) >= 2: return Route("person_disambig", {"candidates": cands})
        if len(cands) == 1: hits.append(cands[0])
    if len(hits) == 1: return {"entity_id": hits[0]["entity_id"], "name": hits[0]["name"]}
    if len(hits) >= 2: return Route("person_disambig", {"candidates": hits})
    return None
```
For "is koutis working on machine learning" → subject "koutis" → resolves Koutis. For the rewrite variant
"is koutis currently working on machine learning" → subject "koutis currently" → "koutis" resolves,
"currently" matches nobody. **The §8 router test MUST use the bare-surname string end-to-end** (through
`resolve_query` → `route`), not a pre-baked full name — else the test passes while the live path fails.

### 5b. The gate `area AND (named OR _PERSON_AREA_PRED)` — regex + neighbor safety
`_PERSON_AREA_PRED = re.compile(r"\b(?:is|are|does|do|did|has|have|was|were)\b", re.I)` (N3) — `area`
already implies an area-trigger verb was captured, so the predicate only needs the subject-inversion
auxiliary. False positives are harmless (they fall through when resolution returns None). Neighbors
(all verified safe by senior-eng):
- `who works on machine learning` — no aux, `named` empty → **skip** → population (unchanged).
- `does CS work on ML` — aux fires but subject "cs" resolves nobody → **fall through** → org population.
- `research areas of the professors in X` — `area` is None → branch skipped → `faculty_areas_in_department`.
- **rewrite-failed raw pronoun** "is he working on ML" — `_PRONOUN_SUBJ` in `_resolve_surname`/subject →
  no resolve → falls through to **today's behavior** (population), never a wrong person. (Add as a test.)
- **declarative rewrite** "Koutis works on ML?" — no aux, no full name → won't fire → population. Accepted.

## 6. Rendering (`v2/core/retrieval/structured_answer.py`) — DETERMINISTIC, basis-aware  *(R2/B2, all 3)*
Wire into `format_answer` (a real branch — else `facts` empty → `if not facts: return None` → RAG, N2) and
add to `_DETERMINISTIC_SKILLS` (`structured_answer.py:141`) so `compose_from_rows` is skipped and the
answer is never reworded (N2). **Plain, no greeting** (Fable — the "Hi there!" lives in the compose path
this deliberately skips; every deterministic answer is plain today). Gender-neutral "their"; echo the
**matched canonical tag** from `person_areas`, not the raw query abbreviation.

- **yes / `basis=="tag"`**: `Yes — {matched_tag} is among {Name}'s listed research areas.`
  + if other `person_areas`: ` Their listed areas: {a, b, c}.`
- **yes / `basis=="prose"`**: `Yes — {area} appears in {Name}'s research profile.`
  + if `person_areas`: ` Their listed research areas: {a, b, c}.`
  *(does NOT claim the area is a listed tag — faithful even for an incidental prose mention; kills the
  self-contradiction where the claim's own follow-up list omitted the area.)*
- **no** (`answer=="no"`): `I don't see {area} among {Name}'s listed research areas. Their listed areas: {a, b, c}.`
  *(areas list is MANDATORY here — lets the user judge, the right hedge given FTS brittleness on multiword areas.)*
- **unknown** (`answer=="unknown"`): `I don't have research areas listed for {Name}, so I can't confirm
  whether they work on {area}.` *(no entity-card suffix — `deterministic_suffix` doesn't handle this skill;
  N5, dropped rather than implied-unbuilt.)*

## 6b. Follow-up / A3 chain wiring  *(N1 — load-bearing, was missing)*
Add `does_person_research_area` to `structured_answer._PN_SINGLE_NAME` (~line 165) so
`person_names_of` returns `[result["name"]]` → the assistant turn is tagged with the person →
a SUBSEQUENT pronoun follow-up ("is he also doing Y") keeps the antecedent. Assert in tests.

## 7. What this does NOT do (explicitly deferred)
- **Gap #2** (clarify when a pronoun is genuinely unresolvable) — separate change ([[project_person_area_yesno_bug]]).
- **Pointed multiword areas** ("is X working on graph machine learning") may FTS-miss and degrade to an
  honest "no" + areas list — never to a wrong "yes"; consistent with the population skill. Accepted (N/RAG §4).
- No change to population/org/faculty routing, the rewrite layer, or `research_of_person`.

## 8. TDD plan
- **skill unit** (`test_skills.py`): Koutis+"machine learning" → yes; a quantum-only person → no (+lists
  areas); zero-area person → unknown; **prose-only/empty-tag person → yes with `basis=="prose"`** (asserts
  the answer does NOT claim a listed area); **incidental "applications in ML" statement → yes/prose**;
  the consistency invariant asserted directly (membership set == `people_by_research_area` filtered to id).
- **router** (`test_router.py`): **bare-surname end-to-end** `resolve_query`→`route`
  "is koutis working on machine learning" → `does_person_research_area{entity_id,name,area}`;
  "is koutis currently working on machine learning" (rewrite variant) → same; "who works on machine
  learning" → `people_by_research_area` (pinned unchanged); "does CS work on ML" → population;
  raw-pronoun "is he working on ML" (rewrite-failed) → falls through, no wrong person; ≥2-surname →
  `person_disambig` w/ origin=does_person_research_area.
- **structured_answer**: deterministic render for tag-yes / prose-yes / no / unknown; `is_deterministic`
  True; `format_answer` returns non-empty for all four; `person_names_of` returns the one name (N1).
- **guard** (N1-adjacent): assert `answer=="no"` never co-occurs with an area word-matching `person_areas`.
- **eval** ([[feedback_grow_correctness_suite]]): add the 2-turn Koutis sequence + a negative to `eval/questions.txt`.

## 9. Goals checklist (per [[feedback_review_against_plan]]) — SHIPPED 2026-07-04 (TDD, 24 tests green)
- [x] person+area → per-person yes/no, **firing on the bare-surname rewrite** (§5a B1) — live-verified
      "is koutis working on machine learning" → does_person_research_area → "Yes …"
- [x] consistency with population roster (§3) — `test_membership_matches_population_skill_exactly` (both directions)
- [x] deterministic render, no fabrication; basis-aware wording (§6 R2/B2) — tag/prose split, prose says "appears in profile"
- [x] honest-partial when no areas (§4 unknown) — `test_unknown_*`, no false "no"
- [x] A3 follow-up chain preserved (§6b N1) — `_PN_SINGLE_NAME`, `test_person_names_of_tags_single_name`
- [x] deterministic wiring: `_DETERMINISTIC_SKILLS` + `format_answer` branch (§6 N2) — asserted, never empty
- [x] population / org / faculty routing unchanged (§5b) — live: "is he working on ML" + "does CS work on ML" → people_by_research_area
- [ ] Gap #2 clarify — **DEFERRED, explicitly** (§7)

**Verification:** 24 new tests green (skill 9 · router 8 · structured 7). Full touched-module regression:
the only failures (test_retrieval_grouping ordering pollution + a pre-existing surname-research route) were
PROVEN pre-existing by an A/B at HEAD with the same file list. Live-DB spot checks: tag-yes, no+list, pronoun
fall-through all correct. Eval Qs appended to `eval/questions.txt`. Fable signed off design + TDD suite;
senior-eng + RAG reviewers CHANGES-REQUIRED all folded in.
```
