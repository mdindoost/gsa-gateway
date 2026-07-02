# Constrained-JSON Slot Extraction — Fallback for KG Slot-Filling (Design, rev2)

Date: 2026-07-01 · Status: DESIGN rev2 (reviews folded; ONE owner decision open — §7) · Workstream 1
Author: Claude (Opus 4.8) · Reviews: senior-eng GO-WITH-CHANGES · RAG/LLM GO-WITH-CHANGES · Codex GO-WITH-CHANGES
Owner sign-off: APPROVED 2026-07-01 — **Option A** (rescope-measurable) chosen; slot-F1 gate deferred to fast-follow after the 39 blind rows are labeled. Build TDD → show diff → commit.

## 1. Problem & goal

Family classifier (`route_classifier.py`) picks the coarse family correctly. Once family=KG, the
specific `{skill, slots}` is extracted by brittle regex in `router.py route()`; natural paraphrases
get the right family but wrong/missing slots, so `route()` returns `None` and the query silently
degrades to RAG. Add a **constrained-JSON slot-extraction FALLBACK** so KG-family questions reliably
produce a **validated** `{skill, slots}`, without rebuilding the router and NEVER executing a skill
with an unvalidated slot.

Motivating failures (regression cases): "which prof does ML in computing" · "can you tell me a bit
about professor Koutis?" · "I'm trying to reach someone named Koutis".

**Do-not-touch:** family classifier, retrieval, generation answer path, existing regex `route()`
fast path. No new framework. Local only (Granite `granite4:tiny-h` + Ollama).

## 2. Phase-0 facts this builds on
- `route()` resolves org/person/metric to real IDs BEFORE returning a `Route`; else `None`. → the
  only fallback trigger is **`route()==None`**. (`area` is a deliberate pass-through — see §5 area guard.)
- Insertion seam is singular & additive: `unified_router.resolve_kg()` turns `None`→`RAG/general`.
- Ollama structured output (VERIFIED vs live docs): top-level `"format"` field w/ a JSON-schema
  object on `/api/generate`, `stream:false`, `temperature:0`.
- Fail-safe boundary already exists: `decide()` is wrapped in try/except → legacy path
  (`message_handler.py:266`), and KG execution catches → RAG (`message_handler.py:543`).

## 3. JSON schema (constrained output)

Enum derived from ONE shared registry (see §4a) so it can never drift from `dataset.VALID_SKILLS`.
**Scope of THIS PR:** the skills that are actually EXERCISED by the labeled set. `papers_of_person`,
`citation_trend_of_person`, and the `mode`/`year` slots have **0 labeled rows** and `papers/trend`
are rejected by `dataset.VALID_SKILLS` — they are **DEFERRED (loud)**, NOT in this schema. The regex
`route()` still handles them as today (unaffected). Slots stay natural-vocab (match the labels).

```json
{
  "type":"object","required":["skill","slots","confidence"],
  "properties":{
    "skill":{"type":"string","enum":[
      "entity_card","research_of_person","metric_of_person","link_of_person","people_by_role",
      "people_by_name","faculty_in_department","people_in_org","officers_in_org",
      "top_people_by_metric","people_by_research_area","count_people_by_research_area",
      "areas_in_org","area_counts","faculty_areas_in_department","people_by_area_tag",
      "org_departments","none"]},
    "slots":{"type":"object","additionalProperties":false,"properties":{
      "person":{"type":"string"},
      "org":{"type":"string"},
      "area":{"type":"string"},
      "metric":{"type":"string","enum":["citations","h_index","i10_index"]},
      "profile":{"type":"string","enum":["scholar","linkedin","orcid","github","website"]},
      "role":{"type":"string"},
      "order":{"type":"string","enum":["asc","desc"]},
      "n":{"type":"integer"}}},
    "confidence":{"type":"number"}
  }
}
```
(`order` added per RAG #7 so "least/min cited" is captured and DECLINED, not silently answered as
descending. `mode`/`year` dropped — deferred with papers/trend.)

Prompt (system): terse per-skill spec + required slots + a PINNED few-shot list drawn ONLY from the
train split, asserted to share **no entity/group** with the 97 blind-test rows (RAG #5). Rule:
"extract only what's explicit; unsure ⇒ skill=none; never invent a name/org." Temperature 0.

## 4. New module `v2/core/retrieval/slot_extractor.py`
`extract_slots(message, generate_json_fn) -> ExtractResult{skill,slots,confidence}`.
- `generate_json_fn(system,prompt,schema)->dict|None` is INJECTED (unit-testable w/ a stub).
- Any failure (Ollama down / invalid JSON / schema-invalid / unknown skill) ⇒
  `ExtractResult("none",{},0.0)`. **Never raises.**

**4a. Shared skill registry.** New `KG_SKILL_NAMES` constant (single source) imported by BOTH the
schema builder and `v2/eval/router/dataset.VALID_SKILLS` — kills the 3-way enum drift (RAG #6).

**4b. Ollama call — SYNC (fixes the async blocker, senior-eng #1 / Codex #8).** `decide()`/`resolve_kg`
are synchronous (called without `await`), so we CANNOT drive aiohttp/`_get_session` from there. Add a
**standalone synchronous** `generate_json(system,prompt,schema,timeout)` using `urllib`/`requests`
POST to `/api/generate` with `format=schema`, `options.temperature=0`, `stream:false`, short
`num_predict`, tight timeout. It does NOT reuse the async client. Blocks the loop for ≤timeout on the
fallback path only (acknowledged; tight timeout mitigates).

## 5. Resolve-and-validate (natural slots → skill args) — the KG guard
Opens its **own short-lived sqlite conn** (senior-eng #6; pattern from `_route`). Reuses the exact
resolvers `route()` uses. Never guesses.

| skill | required | resolver → args | guards replicated from route() |
|---|---|---|---|
| entity_card / research_of_person / people_by_name | person | `persons_in_query`→`persons_by_lastname`→`resolve_people` ⇒ entity_id(+name) | ≥2 ⇒ `KG/person_disambig`(candidates) |
| metric_of_person | person, metric | person⇒entity_id; **metric by `Metric.key` via `metric_fields()`** (NOT match_metric) ⇒ field_key+metric_key | order=desc ⇒ decline (metric_descending_unsupported) |
| link_of_person | person, profile | person⇒entity_id; profile key⇒field_key | |
| people_by_role | role (org opt) | `_ROLE_VOCAB`/`_ROLE_SYNONYM`⇒role_head; **org via `_find_org`**; `_climb_to_scope` | `_LEADERSHIP_PROCESS` shapes ⇒ none (delegated to conf+hardneg gate) |
| faculty_in_department / people_in_org / officers_in_org / areas_in_org / area_counts / faculty_areas_in_department / org_departments | org | **org via `_find_org`** (longest-phrase, NOT exact resolve_org) ⇒ org_id | **org_departments: require `_has_child_departments`**; **people_in_org: require `not _is_university_root`** (senior-eng #5) |
| people_by_research_area / count_people_by_research_area / people_by_area_tag | area | area passed to FTS; org via `_find_org` if present | **area FP guard (below)** |
| top_people_by_metric | metric (org opt) | metric by key; org via `_find_org` or root default; `n` from `_parse_topn(message)` | order=desc ⇒ decline |

**Key fixes folded:**
- **Org resolution uses `_find_org` (natural-text longest-phrase), not exact `resolve_org`** (Codex #3 —
  this is the actual "ML in **computing**" bug).
- **Metric resolves by `Metric.key`** directly (Codex #4 / senior-eng #2 — underscore enum forms have
  no aliases so `match_metric` would fail 2/3 metrics).
- **Ambiguous person ⇒ `KG/person_disambig`** (renders candidates), never `family=CLARIFY` (static)
  (senior-eng #4 / Codex #7).
- **`order=desc` ("least/min") ⇒ decline** (metric_descending_unsupported), matching current `route()`
  (RAG #7).
- **AREA FALSE-POSITIVE GUARD (RAG #8 / Codex #2 — the one real over-fire hole):** a bare area-only
  fire (no org) executes only if the area has **≥ MIN_AREA_SUPPORT** FTS hits in the KG; else ⇒ none⇒RAG.
  With a resolvable org, execute normally. This closes the "how do I learn ML"⇒`people_by_research_area`
  hole.
- **`n`/mode DERIVED from message** via `_parse_topn`/`_paper_mode` (not trusted from the LLM slot),
  matching route() (senior-eng #7).

**Outcomes:** all required slots resolve ⇒ `KG(skill,args)`. Required slot present but unresolvable ⇒
`KG/person_disambig` (people) or none⇒RAG (others). skill=none OR confidence<τ ⇒ RAG.

## 6. Wiring — the only control-flow change
`UnifiedRouter` gets an ollama handle threaded in (senior-eng #3): update
`maybe_build_unified_router(db_path, embedder, intent_detector, ollama)` (`assistant.py`) +
`UnifiedRouter.__init__(..., generate_json)`. Then:
```
def resolve_kg(self, message):
    rt = self._route(message)                                 # regex fast path — UNCHANGED
    if rt is not None: return KG(rt.skill, rt.args)
    ext = extract_slots(message, self.generate_json)          # NEW, only on route()==None
    if ext.skill=="none" or ext.confidence<self.tau: return RAG("general")
    resolved = resolve_and_validate(self.db_path, ext.skill, ext.slots, message)  # own conn
    if resolved is None: return RAG("general")                # unresolved non-person ⇒ RAG
    return resolved                                            # KG(skill,args) or KG/person_disambig
```
`fast_path`/classifier/RAG/COMMAND/OTHER paths unchanged. Extractor fires ONLY when classifier said
KG and regex found nothing.

## 7. ⚠️ OPEN OWNER DECISION — merge-gate scope
RAG review (BLOCKERS #1–#4): the bakeoff harness computes NONE of the slot metrics today
(`metrics.py` has only family/skill accuracy), the **39 blind-test KG rows carry no slot labels**, no
arm wires the extractor, and hardneg isn't scored. So the §7 slot-F1 gate is **not measurable without
net-new work**: (i) build a routing-slot scorer + per-skill P/R/F1 + latency timer + a new extractor
arm + a hardneg pass into the harness, AND (ii) **blind-slot-label the 39 KG test rows** (needs the
owner, per LABELING_PROTOCOL). TWO ways forward:

- **OPTION A (rescope now, recommended):** merge gate = the MEASURABLE-NOW set — (a) NO family-accuracy
  regression on blind test, (b) 3 regression paraphrases route correctly, (c) resolver-rejection unit
  tests (hallucinated org/person/area/desc-order all abstain), (d) a hardneg pass = 0 new KG mis-fires.
  Build the extractor-arm + hardneg pass + latency timer (cheap). **DEFER (loud)** the slot-F1 gate
  until the 39 rows are blind-slot-labeled (fast-follow, needs owner labeling). Ships the mechanism
  now with real safety gates.
- **OPTION B (full gate first):** before merge, build the full slot/per-skill/latency scoring AND you
  blind-slot-label the 39 KG test rows, so slot-F1-improves is a hard gate. Higher rigor, but adds
  net-new harness work + your labeling time up front, delaying the mechanism.

Either way: fix everything in §3–§6, calibrate τ (§8), add the extractor arm + hardneg pass.

## 8. Confidence threshold — calibrated, not magic (RAG #9 / [[feedback_use_max_capacity]])
No hardcoded 0.5. Calibrate τ on train/val to a target precision using the existing
`abstain.py::calibrate_thresholds` pattern. PRIMARY false-positive guard = the resolver rejection +
the family-classifier margin; the LLM's self-reported `confidence` is a SECONDARY signal only (a 4B
model at temp0 is poorly calibrated). τ stored in settings (tunable, kill-switchable).

## 9. Risks / mitigations
LLM over-fires on RAG-ish text ⇒ τ + skill=none + resolver hard-reject + area-support guard; only
runs after classifier=KG. Latency ⇒ fallback-only, temp0, short num_predict, tight sync timeout,
Ollama-down⇒none⇒RAG. Hallucinated org/person/metric/role ⇒ closed resolvers reject; never execute.
Enum/label drift ⇒ single shared registry (§4a).

## 10. Goals checklist (shipped-in-design / deferred-loud)
- [x] Strict JSON schema, enum from shared registry, +`order`, papers/trend/mode/year deferred — §3
- [x] Sync Granite structured call (`format` verified), fail-safe non-raising — §4/§4b
- [x] Extractor returns {skill,slots,confidence}|none — §4
- [x] Regex route() kept; extractor only on None — §6
- [x] Slots validated via existing resolvers; `_find_org` for org; metric-by-key; area-support guard;
      route() negative guards replicated; ambiguous⇒person_disambig; own conn — §5
- [x] none/low-conf ⇒ RAG (unchanged); τ calibrated not magic — §6/§8
- [x] UnifiedRouter ollama wiring specified — §6
- [ ] **OWNER DECISION** merge-gate scope (Option A rescoped-measurable vs B full-gate+labeling) — §7
- [x] 3 regression paraphrases added — §7
- Deferred (LOUD): papers_of_person, citation_trend_of_person, mode/year slots (0 labeled rows +
  VALID_SKILLS rejects) → stay on regex route() only; slot-F1 gate deferred under Option A until the
  39 blind-test KG rows are slot-labeled.
