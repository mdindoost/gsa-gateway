# Router D+E — org/role collision & terse officer forms (design)

**Date:** 2026-07-03
**Author:** Kavosh maintenance (Mohammad Dindoost, owner)
**Status:** Approved design → pending senior-eng + RAG review → TDD build
**Scope:** one file — `v2/core/retrieval/router.py` (+ `eval/questions.txt`)
**Workstream:** short-query correctness + follow-up (threads D+E). A shipped (`d7ef41f`). F/B follow.
**Supersedes:** the stale `2026-07-03-short-query-expander-replacement-design.md` (proposed a
rejected GSA alias table — discard, do not build).

---

## 1. Problem

Two confirmed, precise router gaps. Both are deterministic-routing bugs; neither is an LLM or
data problem.

### D — org+role collision (`president` only)
`_find_org` (`router.py:301`) returns the single **longest** whole-word org match. Five org
names/slugs are bare role words, all `type=office`: `president`(52), `provost`(53),
`registrar`(24), `bursar`(17), `dean-of-students`(20).

When a query names a real org **and** a role word that is also an office slug, the longer office
slug steals the org slot:

> "who is the gsa president" → `_find_org` matches `gsa`(2) and slug `president`(52); `president`
> (9 chars) > `gsa` (3) → `org_id=52`; officer branch fires → `officers_in_org(52)` → terminal
> deflection "I don't have officer information for Office of the President." **(wrong)**

**The collision is `president`-only.** `provost`/`registrar`/`dean-of-students` are already
handled correctly by the existing role branch (`router.py:558-585`) + `role_is_org` guard
(line 568), which use the office org as the correct `people_by_role` scope. Verified live:
`"who is the njit registrar"` → Trombella; `"dean of students at njit"` → Boger;
`"who works in the registrar office"` → the 6-person roster. **Those must not regress.**

`president` is the outlier because it is an *officer* title (handled by the officer branch, not
the role branch) and is absent from `_ROLE_VOCAB`, so there is no office-scoped path that saves it.

### E — terse officer forms (no verb)
The officer branch (`router.py:549`) requires a who/list verb (`_OFFICER_IDENTITY`). Terse forms
have none, and `officer`/`president` are not in `_ROLE_VOCAB`, so:

> "gsa officers", "gsa president", "gsa treasurer", "gwics officers" → `route()=None`.

(Terse **non-officer** roles already work via the bare-org fallback at line 569: "ywcc dean",
"cs chair" route today. E is about **officer** terse forms only.)

---

## 2. Design

### D — carry a non-officer-office alternate; `_find_org` untouched

`_find_org` stays exactly as-is (leaving it untouched is what prevents the registrar/DoS
regressions — those paths depend on the office org winning).

Add a helper that returns the **longest whole-word org match whose matched phrase is NOT a bare
officer-title office**. "Bare officer-title office" is detected by a **`fullmatch`** of the phrase
against the officer-title words (bare "president"/"vice president"/"vp"/"treasurer"/"secretary"),
**not** a `search` over both regexes — `search` would wrongly flag multi-word office names like
"office of the registrar" / "office of the president" and any future org whose *name contains*
dean/director/chair.

In practice only `president`(52) is such an office today, so the alternate is "the longest org
match that isn't org 52-by-bare-president".

Consume the alternate in exactly two places — **the officer-identity branch (549) and the new
terse branch**:

> If the resolved `org_id`'s matched phrase is a bare officer-title office **and** a distinct
> non-officer-office org also matched → use the alternate org for the officer route.

- "gsa president" → alternate `gsa`(2) → `officers_in_org(2)`. ✓
- "gwics president" → alternate `gwics`(11) → `officers_in_org(11)`. ✓
- "njit president" → alternate `njit`(root) → `officers_in_org(root)` → surfaces Teik C. Lim
  (`admin` edge on root). ✓
- "president office hours" / "office of the president" → **no** distinct alternate → stays
  org 52 → today's behavior (`route()=None` → RAG/live). ✓ (preserves the `role_is_org`
  intent without touching that guard)

The role branch (558-585) is untouched → registrar/DoS/provost keep their office scope.

### E — terse officer branch, gated to real-officer orgs (Option A)

New branch placed **after the role branch (after line 585), before the B3 org-enumeration branch
(587)**. Precedence matters: a query carrying both vocabularies ("cs chair secretary") must hit
the role branch first (→ `people_by_role("chair", cs)`), not the terse officer branch.

Fire `officers_in_org(org_id)` (org_id = the D-corrected org) **iff all** hold:

1. `_OFFICER_TITLE.search(q)` — a bare officer/president/vp/treasurer/secretary/… word is present.
2. `not _OFFICER_PROCESS.search(q)` — no impeach/elect/eligible/duties/etc.
3. **title-is-org guard:** the matched title word is **not** contained in the resolved
   `org_phrase`. Kills "president office hours" (org 52, title == org) → falls through.
4. **zero-residue guard:** strip the org phrase and the matched `_OFFICER_TITLE` span from `q`;
   fire only if **every** remaining token is a stopword. Mirrors the existing `_is_bare_name`
   helper (all-tokens-accounted-for). "the gsa president" → residue empty → fire; "former gsa
   president" / "gsa president salary" / "past gsa presidents" → non-stopword residue → RAG.
5. **real-officer gate (Option A):** the org has ≥1 active `has_role` edge with
   `category IN ('officer','deprep')`. Same query shape as `skills.py:221-228`, narrowed to the
   two true-officer categories:
   ```sql
   SELECT 1 FROM edges e JOIN nodes o ON o.id = e.dst_id
   WHERE e.type='has_role' AND e.is_active=1
     AND e.category IN ('officer','deprep')
     AND json_extract(o.attrs,'$.org_id') = ?
   LIMIT 1
   ```

**Why the gate:** `has_role.category` is a clean closed vocabulary. `officer`/`deprep` = true
officers, present on exactly 5 orgs (GSA + GWICS + Grad BME Society + Iranian Cultural Assoc +
Sanskar); `admin` (deans/provost/president) is disjoint. Gating to officer/deprep makes the *new*
branch deterministically unable to emit a mislabeled college-leadership roster (the dangerous
false-positive class). The gate is **category-driven and org-blind** — GSA and every club pass
identically; no GSA bias.

- "gsa officers", "gsa president", "gwics officers" → fire. ✓
- "ywcc officers", "cs officers" → gate fails → RAG. Colleges have deans/chairs (already reachable
  via "ywcc dean" / "cs chair"); no capability lost. ✓
- "gsa events" → not an officer title → no fire. ✓

---

## 3. Non-goals / explicitly deferred (flagged, not dropped)

- **Verb-ful officer-branch mislabel (LIVE bug, unchanged).** "who are the ywcc officers" is wrong
  *today* — `officers_in_org` includes `admin` edges and renders `titles[0]` (a faculty title, e.g.
  Wu/Wang's professor title instead of "Associate Dean"). This design does **not** fix it (Option A
  gates only the new terse branch). **Deferred follow-up:** pick the `admin`-matching title from
  `attrs.titles`, or exclude `admin` from `officers_in_org` and route college-leadership asks
  elsewhere. Tracked, not silent.
- **`provost` = no-op.** Pelesko's provost edge sits on org 47 (NJIT Senior Administration), not
  org 53, so "who is the provost" is empty before and after (subtree-scope fix out of D+E scope).
- **`bursar`** matches neither role vocab nor officer titles → never involved. Listed for honesty.
- **Bare "president"/"officers" with no org** → out of scope (thread F, confidence-gated clarify).
- **`president` is NOT added to `_ROLE_VOCAB`** — the officer branch handles it.

---

## 4. Testing

TDD. Unit tests against `route()` and the new helper:

**D (fire correctly):** "who is the gsa president" → `officers_in_org(gsa)`; "gwics president" →
`officers_in_org(gwics)`; "njit president" → `officers_in_org(root)`.
**D (no regression):** "who is the njit registrar" → `people_by_role("registrar", 24)`; "dean of
students at njit" → `people_by_role("dean of students", 20)`; "who works in the registrar office"
→ `people_in_org(24)`; "president office hours" / "office of the president" → `route()` unchanged.
**E (fire):** "gsa officers", "gsa president", "gwics officers" → `officers_in_org(<club>)`.
**E (guards):** "ywcc officers" / "cs officers" → `None` (gate); "president office hours" → `None`
(title-is-org); "former gsa president" / "gsa president salary" → `None` (zero-residue); "gsa
events" → not officer route.

**Eval additions** (`eval/questions.txt`, per feedback_grow_correctness_suite):
`who is the GSA president` · `gsa president` · `GSA officers` · `gwics officers` · `ywcc officers`
· `cs officers` · `ywcc dean` · `who is the njit registrar` · `president office hours` ·
`registrar office hours` · `former gsa president`.

---

## 5. Goals checklist (shipped vs deferred)

| Goal | Status |
|---|---|
| D: "gsa/gwics/njit president" resolve to the right org's officers | **shipped** |
| D: registrar / dean-of-students / "registrar office" — no regression | **shipped** (untouched) |
| D: "president office hours" — no false positive | **shipped** (guard) |
| D: provost collision | **deferred** — data on org 47, subtree scope out of scope |
| E: "gsa/gwics officers", "gsa president" terse → officers | **shipped** |
| E: college "officers" don't mislabel (ywcc/cs) | **shipped** (Option A gate → RAG) |
| E: process/attribute/tense terse forms → RAG | **shipped** (guards) |
| Verb-ful officer-branch admin-title mislabel | **deferred** — separate follow-up (loud) |

---

## 6. Guiding-principle compliance

- **Deterministic, no LLM** — all rule-based; no new model call. ✓
- **Conservative / false-positive is the dangerous failure** — every new fire path is guarded
  (title-is-org, zero-residue, real-officer gate); ambiguous/attribute forms fall to RAG. ✓
- **GSA-equal, no bias table** — D is org-blind (real named org wins); E's gate is category-driven
  and applies identically to GSA and every club. ✓
- **Bare-term ambiguity out of scope** — deferred to thread F (clarify). ✓
