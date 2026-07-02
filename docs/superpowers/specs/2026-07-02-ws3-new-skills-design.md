# WS3 — Three evidence-backed KG skills (contact / orgs-by-type / title)

**Status:** design, awaiting owner sign-off → HARD-GATE review → TDD build.
**Workstream:** Kavosh v2.5 · pillar 4 (routing/intent) · WS3.
**Predecessors:** WS1 (constrained-JSON slot-extraction fallback), WS1.5 (measure-then-fix),
WS2 (fuzzy entity + org resolution, live `87eacb6`). WS3 inherits WS2's entity/org resolution
+ corroborate-or-clarify/abstain gate for free.
**Owner spec:** the WS3 message (2026-07-02). This doc is the delta-spec; the message is authoritative.
**Gap evidence:** `docs/routing_handoff_system_report.md` section B (B1, B3, B4) — from ~805 distinct
real questions.

## 1. Goal & scope

Add the **three** structured KG skills the data already supports (no crawl, no schema change), wire
them into the router + slot extractor, and inherit WS2 resolution. Explicitly **do not** touch
retrieval, generation, or the family classifier's coarse KG/RAG decision beyond adding these three as
route targets.

| Skill | Signature | Slots | Data (verified live) |
|---|---|---|---|
| **B1** `contact_of_person` | `(conn, entity_id) -> dict` | `{person}` | 1077 email / 742 phone / 392 office (of 1186 Person nodes) |
| **B3** `orgs_by_type` | `(conn, org_type, parent_org_id=None) -> list[str]` | `{org_type, parent_org?}` | club=5, department=16, college=6 |
| **B4** `title_of_person` | `(conn, entity_id) -> dict` | `{person}` | 1215 `has_role` edges carry `attrs.titles` |

Out of scope (report names them, owner excluded them): **B2** office_contact (data PARTIAL, may need a
field), **B5** count_people_in_org (low priority; a count flag on existing list skills), **B6**
find_advisor (the parked Find-Your-Advisor project).

## 2. Locked design decisions

1. **B3 render = count + list combined.** Always `There are N <type>s at NJIT: A, B, C.` — one answer
   serves both "how many clubs" and "list clubs". Justified by tiny cardinalities (5/6/16, all fit one
   reply) and zero render-time phrase-matching (deterministic). Revisit only if a future org_type is large.
2. **B3 coexists with `org_departments` (does NOT subsume it).** Unanimous across senior-eng + RAG +
   Codex review. `org_departments` stays the routed skill for "departments under X" (keeps its labeled
   gold rows, its route site, and its `_has_child_departments` leaf-department abstain guard). **DRY
   refinement:** `org_departments`'s body delegates to `orgs_by_type(conn, 'department', org_id)` so the
   type-filtered child-enumeration SQL lives in one place — DRY win, zero routing/render/gold/guard blast
   radius. Flip-to-subsume conditions (documented, none currently true): the router rebuild becomes part
   of this workstream; eval shows scoped-vs-unscoped dept margins collapsing post-masking; or many more
   org types get added.
3. **B3 `org_type` enum = {club, department, college}.** The three with real demand + clean data.
   "student organization(s)" → club. `office` (B2 territory) excluded. Open build-time nuance (not a
   locked decision): `school` is a distinct org type (2 rows) — decide at build whether "list the
   schools" maps to `college`, returns `type='school'` separately, or abstains; default = abstain
   (safe) unless review prefers a synonym.

## 3. Skill functions (Phase 1) — house style: `conn + resolved args → rows/dict`

### B1 `contact_of_person(conn, entity_id) -> dict`
Reads `person_attrs(conn, entity_id)` (the single per-person JSON reader). Returns
`{name, email, phone, office, present: [fields...]}` where each field is the value or `None`, and
`present` lists which fields exist.

**Anti-fabrication (honest-partial, matches `faculty_areas_in_department`):**
- No fields present → honest "no contact info on file", NOT a blank success.
- Partial (e.g. office but no email) → render what exists AND state what's missing ("Email: not on
  file"). Never imply a contact channel exists when it doesn't.

### B4 `title_of_person(conn, entity_id) -> dict`
Reuses the `entity_card` titles-iteration (has_role edges, `titles = attrs.titles or [cat]`, per org).
Returns `{name, titles: [(title, org_name), ...]}`. Honest-empty (`titles: []`) if the person holds no
roles — render "I don't have a listed position for {name}", never fabricated.

### B3 `orgs_by_type(conn, org_type, parent_org_id=None) -> list[str]`
Mirrors `org_departments`'s SQL, generalized on `type`:
```sql
SELECT name FROM organizations
WHERE type = ? AND is_active = 1
  AND (:parent IS NULL OR parent_id = :parent)
ORDER BY name
```
`org_type` validated against `{club, department, college}` (caller-side guard; unknown → abstain).
`org_departments(conn, org_id)` becomes `return orgs_by_type(conn, 'department', org_id)`.

## 4. Routing (Phase 2)

### 4a. `route()` fast-path (router.py) — deterministic regex, ordered to avoid cannibalizing neighbors
New cue detectors, placed **before** the generic "who is X" → `entity_card` fall-through:
- **B1** contact cue (`email`, `e-mail`, `phone`, `contact`, `reach`, `how (do|can) i (contact|reach)`)
  + a resolved named person → `Route("contact_of_person", {entity_id, name})`.
- **B4** title cue (`title`, `position`, `what (is|'?s) .*'?s (title|position)`, `what does <name> do`)
  + a resolved named person → `Route("title_of_person", {entity_id, name})`.
- **B3** type-enumeration — **tightened per review (over-match blocker):** fire ONLY on an explicit
  enumerate-verb *bound to a plural type noun* — `(list|name|show|how many|which|what) … (clubs|colleges)`
  or `(clubs|colleges) … (are there)` / `student organizations`. **Drop bare `what`/`which`
  co-occurrence** (else "which college is X in", "what college should I apply to" mis-route). Only
  `club` and `college` fire here; **`department` stays entirely on the existing `_DEPT_ENUM`+`not
  _FACULTY_CUE`+`org_id is not None` branch** (do NOT add an unscoped all-departments route — it
  cannibalizes "which department is Koutis in"). Parent: pass `parent_org_id` ONLY when an org
  resolves AND is a *plausible* parent (not the university root for a college query); otherwise
  `None` (abstain from silent mis-scoping). `org_type` validated against the shared
  `ORG_TYPE_ENUM = {"club","department","college"}` constant (used in skill + router + extractor).

**Disambiguation contract (must hold in verification):**
| Query | Skill |
|---|---|
| "who is Koutis" | `entity_card` (unchanged) |
| "Koutis's email" / "how do I contact Koutis" | `contact_of_person` |
| "what is Koutis's title" / "what does Koutis do" | `title_of_person` |
| "who is the chair of CS" | `people_by_role` (unchanged — role→person, no named person) |
| "departments in YWCC" | `org_departments` (unchanged) |
| "what clubs are there" / "list student organizations" | `orgs_by_type(club)` |

**Guards (inherit, do not weaken):**
- Bare-pronoun / context-dependent ("what is his position", "who do I contact about this") stay
  **out of KG**. NOTE (review correction): `_FOLLOWUP_RX` matches only "what about/how about/who
  else…" — it does NOT match these pronoun phrases. Today they stay out of KG only *incidentally*
  (no surname resolves from "his"/"this"). WS3 makes this **explicit**: a bare pronoun subject
  ("his"/"her"/"their"/"this") with no named person → `route()` returns None (RAG) and the extractor
  abstains; add a pronoun guard in `_resolve_surname` + router-level hardneg tests, don't rely on the
  data-dependent accident.
- B1/B4 person resolution goes through WS2 `_resolve_person_slot` → ambiguous ⇒ `person_disambig`,
  fuzzy-typo ⇒ corroborate-or-clarify. Never a wrong person.
- B3 multi-match parent or unknown `org_type` ⇒ abstain (⇒ RAG), consistent with WS2.

### 4b. Slot extractor (constrained-JSON fallback, fires when `route()`==None)
- `KG_SKILL_NAMES` += `contact_of_person`, `orgs_by_type`, `title_of_person`.
- `REQUIRED_SLOTS`: B1/B4 → `("person",)`; B3 → `("org_type",)`.
- `build_schema`: add an `org_type` string property with `enum: ORG_TYPE_ENUM`; keep optional `org`
  (parent).
- **BLOCKER (all 3 reviewers): `extract_slots` slot whitelist.** `extract_slots` copies only
  `("person","org","area","metric","profile","role","order")` into `clean` — so a model-emitted
  `org_type` is **silently stripped** and B3 abstains every time. **MUST add `"org_type"` to that
  tuple**, and add an **end-to-end test through `extract_slots`** (stub `generate_json_fn`) — the
  direct-`resolve_and_validate` unit tests do NOT catch this.
- **`_SYSTEM` prompt + few-shots (MAJOR):** add one clause per new skill to `_SYSTEM`; add ≥1 B3
  few-shot; and **re-point the existing few-shot** `'I am trying to reach someone named Koutis' →
  entity_card` to `contact_of_person` (that row is literally the WS1 finding B1 fixes).
- `resolve_and_validate`: B1/B4 = a **separate** branch (NOT folded into the `entity_card`/
  `research_of_person` block) that clones the person-resolve (WS2 fuzzy; ambiguous → `person_disambig`)
  but **skips the `_identity_cued` gate** — the contact/title cue IS the intent; add a test that a
  contact query resolves without an identity cue. B3 = new branch: validate `org_type ∈ ORG_TYPE_ENUM`;
  optional parent via `resolve_org_slot()` (named-but-unresolved ⇒ abstain, never default to root).

## 5. Render (Phase 3) — `structured_answer.py`
`run()` data arms + `format_answer()` text arms for all three:
- **B1**: contact block, only fields that exist, explicit "not on file" for asked-but-missing; empty →
  honest no-contact line.
- **B4**: crisp title line(s) — `{name} is {title} at {org}` (join multiples); empty → honest no-position line.
- **B3**: `There are N <type>(s) at NJIT: {list}.` (parent-scoped variant names the parent).

**Compose with greeting (owner decision, 2026-07-02).** B1/B4/B3 are **NOT** added to
`_DETERMINISTIC_SKILLS`; they go through `compose_from_rows` like `entity_card` (and the sibling list
skills `officers_in_org`/`people_in_org`), so they keep the friendly "Hi there!" opener
([[feedback_keep_friendly_greeting]]). `format_answer` still produces the canonical facts string — it
becomes the "Facts" handed to compose AND the offline fallback. Anti-fabrication rides on the SAME
`compose_from_rows` clauses `entity_card` already uses (temp 0.0; MUST NOT add/drop/alter a fact) — the
identical risk profile the owner already accepts for `entity_card`'s email/phone. (Reviewers flagged
deterministic-skip as the stronger value-safety option; owner chose warmth + consistency with
entity_card, accepting the compose clamp.)

## 6. Labeling (Phase 3) — `eval/router/labeled_routes.jsonl`
**Note:** `route_exemplars.py` holds no hardcoded exemplars — it loads them from `labeled_routes.jsonl`
(seeds + TRAIN split; test/hardneg held out). So "add exemplars" and "add labeled gold rows" are ONE
action on this file, feeding both the family classifier and the skill classifier.

Per `LABELING_PROTOCOL.md` / `RUBRIC.md`: ≥~15 rows per new skill where real phrasings exist, mined
from `eval/router/all_questions.jsonl` (prefer real over synthetic; mark provenance `real`/`seed`). Add
a few **hardneg** rows for the bare-pronoun cases so the guard is regression-tested. Do NOT touch
existing test/hardneg rows.

## 7. Verification (full bar — print all)
1. **Case outputs (live DB)** — resolved skill + rendered answer for: "Koutis's email"
   (→contact_of_person), "how do I contact professor Koutis" (+WS2 fuzzy if typo'd), "what clubs are
   there" / "list student organizations" (→orgs_by_type, the 5 clubs), "how many clubs" (count+list),
   "what is Koutis's position" / "what does Koutis do" (→title_of_person).
2. **Disambiguation set** — the §4a table routes correctly (email vs card vs title vs role vs
   departments); new skills don't cannibalize `entity_card`/`people_by_role`/`org_departments`.
3. **Anti-fabrication** — a person with a missing contact field → honest "not on file", never a
   fabricated or blank-success answer.
4. **Regression** — `python scripts/router_slot_bakeoff.py`: NO regression on blind-test family
   accuracy (baseline = whatever the current bakeoff prints post-WS2), NO new hardneg mis-fires; the
   three new skills show non-zero correct dispatch.
5. **Clubs enumerable** — the specific gap closed: `org_departments` couldn't list clubs; `orgs_by_type`
   can.

## 8. Merge gates
- (a) All three skills return correct data on real cases; missing fields handled honestly.
- (b) The disambiguation set routes correctly — no cannibalization of existing skills.
- (c) No regression on blind-test family accuracy or hardneg.
- (d) Clubs are now enumerable.
- Plus HARD-GATE process: senior-eng + RAG review + Codex second opinion, owner sign-off, TDD, diff shown.

## 9. Scope diff (files touched — confirm no retrieval/generation/family-classifier-core change)
- `v2/core/retrieval/skills.py` — +`orgs_by_type`; `org_departments` delegates to it.
- `v2/core/retrieval/entity.py` — +`contact_of_person`, +`title_of_person`.
- `v2/core/retrieval/router.py` — +B1/B4/B3 cue detectors + route sites.
- `v2/core/retrieval/slot_extractor.py` — enum/REQUIRED_SLOTS/schema/resolve_and_validate arms.
- `v2/core/retrieval/structured_answer.py` — run/format_answer arms + `_DETERMINISTIC_SKILLS`.
- `eval/router/labeled_routes.jsonl` — new labeled rows (incl. hardneg pronoun cases).
- `v2/tests/…` — new unit tests per skill + routing/disambiguation + anti-fabrication.
- Auto gate report from `scripts/router_slot_bakeoff.py`.

## 11. HARD-GATE review — findings folded (senior-eng + RAG + Codex, all GO-WITH-CHANGES)

Coexist call: unanimous (§2.2). Full design+plan review verdict: **GO-WITH-CHANGES**, all folded above.
- **[BLOCKER] `extract_slots` strips `org_type`** → B3 fallback dead. Folded §4b (add key + e2e test).
- **[BLOCKER] B3 cue over-broad** (bare what/which + singular college) → cannibalizes RAG. Folded §4a
  (enumerate-verb bound to plural noun; drop bare what/which).
- **[BLOCKER] unscoped-department branch cannibalizes** "which department is X in". Folded §4a
  (dropped; department stays on the existing guarded branch).
- **[BLOCKER] B3 parent scoping too eager** → "list colleges at NJIT" scopes to root. Folded §4a
  (parent only when plausible; else None).
- **[MAJOR] B1 headline under-fires** — add `_CONTACT_CUE` to BOTH person-branch triggers.
- **[MAJOR] extractor `_SYSTEM`/few-shots** — add clauses + B3 few-shot + re-point the Koutis row.
- **[MAJOR] validate `org_type` in the skill** — shared `ORG_TYPE_ENUM` in skill+router+extractor.
- **[MAJOR] bakeoff too weak** — gates only `family_accuracy`; add a per-skill non-regression check
  for `entity_card`/`org_departments` (the skills B1/B4/B3 could cannibalize).
- **[MAJOR] router pronoun hardneg tests** + explicit pronoun guard in `_resolve_surname` (§4a).
- **[MAJOR] title category-fallback wording** — `[cat]` fallback ("faculty") is acceptable
  role-evidence; render as a title-listing (`{name} — {title}, {org}; …`) so "is faculty at" reads fine.
- **[MINOR] "office hours" regression** — narrow `_CONTACT_CUE` so `office` doesn't catch "office hours".
- **[MINOR] render plural hack** → explicit `{type: (singular, plural)}` label map.
- **[MINOR] school→abstain untested** — add hardneg tests/labels ("list the schools" → not orgs_by_type).
- **[MINOR] fixture cleanups** (no-op `_set_attrs`, dead `if False` line).
**Verified sound by senior-eng (no change needed):** delegation byte-identical; `resolve_org_slot` in
scope at the B3 insertion; B1/B4 skip `_identity_cued`; "list faculty in CS department" stays put;
"how many clubs"/"list student organizations" reach B3; `_person_skill` dispatch correct.

## 10. Goals checklist (shipped/deferred — per feedback_review_against_plan)
- [ ] B1 contact_of_person built + honest-partial + routed + rendered + labeled + verified.
- [ ] B3 orgs_by_type built (coexist + delegation) + enum guard + routed + count-list render + labeled + verified.
- [ ] B4 title_of_person built + honest-empty + routed + rendered + labeled + verified.
- [ ] Disambiguation set passes (no cannibalization).
- [ ] Bakeoff: no family/hardneg regression; new skills dispatch non-zero.
- [ ] Clubs enumerable (gap closed).
- [ ] No retrieval/generation/family-classifier-core changes.
