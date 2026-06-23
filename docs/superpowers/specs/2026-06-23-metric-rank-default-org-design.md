# Design Note — Default university-wide scope for bare metric-ranking queries

**Date:** 2026-06-23
**Status:** Reviewed — both reviewers **APPROVE-WITH-CHANGES** (2026-06-23); changes folded in below.
Awaiting owner sign-off before TDD build.

**Review outcomes (folded into this note):**
- **Senior-eng:** APPROVE-WITH-CHANGES — guard must be position-1 + function-level `return None`;
  mandatory person/faculty cue gate; add `_root_org_id` helper (`is_active=1`) + None-guard; nudge
  wires into `format_answer` (deterministic skill, no LLM compose) not `deterministic_suffix`; +5 tests.
- **RAG/LLM:** APPROVE-WITH-CHANGES — same scope-gate conclusion (require person/faculty cue, drop the
  vague "no competing intent" disjunct); **Bug-B served-answer safety**: silent fall-through can't
  fabricate a *ranking* but RAG could still name *a* person as "least cited" — the only hard guarantee is
  an explicit decline; nudge should name the scope + show syntax.
- **Owner decision (post-review):** chose **Option 3 (explicit deterministic decline)** for Bug B over
  the reviewed Option 1 (silent fall-through) — makes "never name a person as least-cited" a structural
  guarantee, not a soft-guard mitigation. Bug B section + tests updated accordingly.
- **Senior-eng re-review of the Option-3 delta:** APPROVE-WITH-CHANGES — `Route → deterministic skill`
  modeled on `person_disambig` is correct; required: (1) gate the decline with `_FACULTY_CUE or
  _PERSON_INTENT` (symmetry with Bug A — else "fewest citations needed to graduate" wrongly declines);
  (2) add to `_DETERMINISTIC_SKILLS` or the LLM is invoked; (3) no baked coverage numbers in the canned
  text; (4) keep `_DESC_DIR` separate from `_RANK_CUE`; (5) +4 tests. All folded in.
**Author:** Mohammad (intent) + Kavosh session trace
**Scope:** small, surgical — all in the `router.py` metric branch: a default-to-root fallback,
a descending-direction guard, one formatter line, one flag. Two related bugs, ONE change set / PR.

---

## 1. The bug(s)

This note covers **two related defects in the same metric branch**, fixed together:

- **Bug A — bare "most cited" misroutes to RAG** (§1A): university-wide metric ranking with no
  org named returns `None` → wrong RAG answer.
- **Bug B — "least cited" misroutes to a faculty dump** (§1B): descending-direction metric
  queries fall through to `faculty_in_department` and list all ~1,076 people.

## 1A. Bug A — bare "most cited"

`who is the most cited professor` returns a **confidently wrong** answer.

Trace (via `scripts/ask.sh "who is the most cited professor" --verbose`):

1. `router.route()` returns **`None`** → the query falls through to semantic RAG.
2. RAG has no notion of citation counts; it returns the top embedding match —
   **Vincent Oria** (an unrelated "who is Prof. Oria?" FAQ chunk) — and the LLM
   answers from it. The number the user asked about (citations) never enters the picture.

The data and the skill are **correct**. The identical question with an org named works:

```
most cited professor at NJIT     → top_people_by_metric → Mengchu Zhou (90,980 citations) ✓
who has the most citations at NJIT → top_people_by_metric → Mengchu Zhou (90,980 citations) ✓
top 5 most cited faculty at NJIT  → top_people_by_metric → Zhou, Biswal, Ansari, Kosovichev, Lanzerotti ✓
```

So this is a **routing gap**, not a data or skill problem.

## 1B. Bug B — "least cited" misroutes to a faculty dump

`least cited professor at NJIT` (also "fewest citations", "lowest h-index") routes to
**`faculty_in_department` with org_id=1** → attempts to list **all ~1,076 NJIT people**.
Confirmed via `scripts/ask.sh`.

Two compounding causes:
1. `_RANK_CUE` (router.py:110) only matches `most|top|highest|ranked|rank` — it has **no**
   `least|fewest|lowest`, so the metric-ranking path is never entered.
2. `top_people_by_metric` (skills.py:261) is hardcoded `... DESC` — there is **no** ascending
   sort, so the skill couldn't answer "least" even if the cue matched.

Because the ranking path is skipped, the query falls through to `_FACULTY_CUE` ("professor")
+ a named org (NJIT) → the faculty roster dump.

**Decision (owner, 2026-06-23): do NOT build ascending ranking.** Reasons:
- **Data sparsity makes it misleading** — Scholar metrics exist for only **211 of 1,076**
  people; the genuinely least-cited almost all have *no* metric (not in the set), so any
  "least cited" answer ranks a partial set and is wrong in spirit. NB: this sparsity is a
  **source/faculty gap**, not a pipeline defect — NJIT profile pages don't list Scholar and
  many faculty haven't published/provided a discoverable Scholar profile. It is NOT in scope
  for this fix (the coverage lever is the separate Scholar-discovery/manual-add path); the
  honest-partial "211 of 1,076" wording is the correct handling.
- **Low-utility / unkind** — publicly naming a specific professor "the least cited" is
  reputationally bad output we don't want to emit.

**Chosen behavior (owner, 2026-06-23): Option 3 — explicit deterministic decline.** Detect the
descending-metric intent and route to a fixed, deterministic decline message — never to RAG.
Rationale: silent fall-through (Option 1) only *mitigates* the reputational harm (naming a real
professor "least cited") via the soft compose guard on an 8B model; Option 3 makes it
**impossible** — no LLM is involved — and gives a better answer (explains the partial data and
offers "most cited"). The owner judged "never name a person as least-cited" a hard line worth the
small extra branch. Ascending *ranking* is still NOT built (the decline replaces it).

## 2. Root cause

`v2/core/retrieval/router.py`, the metric branch (~lines 297–310):

```python
mm = profile_fields.match_metric(q)               # "cited" IS a citations alias → mm set ✓
if mm is not None:
    field_key, metric = mm
    if org_id is not None and _RANK_CUE.search(q): # _RANK_CUE matches "most" ✓  BUT org_id is None ✗
        return Route("top_people_by_metric", ...)
    person = _resolve_person(conn, q, named)       # no name; surname guard rejects (6 tokens > 4)
    ...                                            # → no return → falls through to RAG
```

The metric-**ranking** sub-branch requires `org_id is not None`. A *university-wide* ranking
("most cited professor", no org named) has `org_id is None`, so the branch is skipped, the
person fallback finds nothing, and `route()` returns `None`.

The design already **supports** university-wide ranking — `top_people_by_metric` treats the
NJIT **root** org as university-wide (CLAUDE.md: *"root org = university-wide"*). The gap is
that the router only sets `org_id` when an org is **explicitly named** in the query. There is
no rule that says *"metric + rank cue + no org → default to the NJIT root."*

## 3. Guiding principle (owner)

> Kavosh is the NJIT assistant — everything is NJIT-scoped unless the user narrows it.
> When no org is specified, default to NJIT.

Agreed **as a scoped rule**, not a global one. A blanket "default `org_id` to root for every
branch" would regress other branches that intentionally refuse the root:

| Branch | Bare query, no org | If org_id defaulted to root |
|---|---|---|
| `top_people_by_metric` | "most cited professor" | ✅ correct (university-wide is first-class) |
| `faculty_in_department` | "who are the professors" | ⚠️ dumps all ~1,076 NJIT people |
| `people_in_org` | "who works here" | already **guarded off root** (`_is_university_root`, line 351) by design |
| `org_departments` | "what departments" | lists every college (behavior change) |

Therefore the default is applied **only in the metric-ranking branch**, where the skill
already produces a correct, graceful university-wide answer.

**Clarify-first (ask the student which org) was considered and rejected** for this case:
"most cited professor" has an obvious NJIT-wide reading; Kavosh can't infer a better scope by
asking (no per-user department profile); and it adds a turn + net-new "org-disambig" machinery
for a question that isn't genuinely ambiguous. Ask-back stays reserved for true ambiguity
(e.g. `person_disambig` for a surname matching ≥2 people). Instead we default **and** invite
narrowing in the answer (Change 2).

## 4. The change (2 layers + 1 flag)

### Change 1 — Router (the fix)
**File:** `v2/core/retrieval/router.py`, metric branch.

When `mm` is set, `_RANK_CUE` matches, **no org was named**, AND a **person/faculty cue is
present** (`_FACULTY_CUE.search(q) or _PERSON_INTENT.search(q)`), resolve the NJIT **root** org
and route `top_people_by_metric` with it (and `org_defaulted=True`). Keep the existing
explicit-org path unchanged.

- Root org is **resolved, not hardcoded** — add a `_root_org_id(conn)` helper:
  `SELECT id FROM organizations WHERE parent_id IS NULL AND is_active=1 LIMIT 1` (note: filter
  `is_active=1`, which the existing `_is_university_root` does NOT). Compute it **lazily**, only
  inside this no-org branch. **If it resolves to `None` (misconfigured DB), return `None`** — never
  route with `org_id=None` (that would break `org_descendants`).
- **Scope gate — DECIDED (both reviewers):** require a person/faculty cue
  (`_FACULTY_CUE or _PERSON_INTENT`). This blocks false positives where the broad `cited`/`citation`
  alias + a rank word appear on a NON-person question — "the most cited **paper**", "top **citation**
  **award**", "most cited **study**" — which must go to RAG, not a faculty ranking. (The vague "or no
  competing intent" disjunct from the draft is **dropped** — not deterministically definable.)
- No new skill, no schema change. `top_people_by_metric` already emits honest-partial wording.

### Change 1b — Router + decline route (Bug B, Option 3)
**Files:** `v2/core/retrieval/router.py` (detect) + `v2/core/retrieval/structured_answer.py` (decline text).

When `mm` is set, a **descending-direction word** (`least|fewest|lowest|bottom`) is present, **AND a
person/faculty cue is present** (`_FACULTY_CUE or _PERSON_INTENT`), **route to a deterministic decline** —
`Route("metric_descending_unsupported", {field_key, metric_key})`. This check is the **first statement
inside the `if mm is not None:` block (position-1)**, ahead of BOTH the explicit-org rank branch and the
no-org default — so when it fires it wins **unconditionally**, even if "most"/"top" co-occur and regardless
of whether an org is named (otherwise "least cited professor at NJIT" would still reach
`faculty_in_department` at line 348 and dump the roster). It must `return` this Route, not fall through.

- **Person/faculty cue is REQUIRED (reviewer §3 — symmetry with Bug A).** Without it, a metric alias +
  a descending word on a NON-people question would be wrongly declined: *"fewest citations needed to
  graduate"*, *"lowest h-index that still counts"* (policy/threshold → RAG), *"papers with the fewest
  citations"* (about publications, not faculty). Requiring `_FACULTY_CUE or _PERSON_INTENT` mirrors Bug
  A's gate exactly, so both halves of the `if mm:` block are symmetric. Queries WITHOUT a person cue
  fall through to RAG (return nothing here).
- **Separate regex:** add `_DESC_DIR = re.compile(r"\b(least|fewest|lowest|bottom)\b")`. Do **NOT** add
  these words to `_RANK_CUE` (router.py:110) — Bug A's ascending path depends on `_RANK_CUE` *not*
  matching them.
- **The decline is deterministic (no LLM) — mandatory wiring.** Add `"metric_descending_unsupported"`
  to `_DETERMINISTIC_SKILLS` (`structured_answer.py:96`) so `is_deterministic()` is True and the compose
  gate (`message_handler.py:398`) skips the LLM — otherwise the LLM rephrases the decline and the
  no-name guarantee is lost. Model the route on `person_disambig` (a no-DB-query route): `run()` early-
  returns the args into the result dict (before the `org_id`/`rows` chain), `format_answer` emits the
  canned text. `format_answer` MUST return non-empty (else `_try_structured` drops it to RAG).
- **Canned text — NO baked numbers.** Name the metric (via the existing `_metric_noun(metric_key)` helper,
  `structured_answer.py:113`) and offer the highest-ranked alternative; keep coverage **qualitative**
  (do NOT hardcode "211 of 1,076" — it drifts as Scholar coverage grows; baked numbers violate the
  numbers-from-data convention). e.g.:
  > *"I can only rank people by **highest** {metric} (e.g. most cited, top h-index), not lowest — and my
  > Scholar coverage is partial, so a 'least {metric}' ranking wouldn't be meaningful. Want the
  > **most {metric}** instead?"*
- No ascending sort is added to `top_people_by_metric` — the decline replaces ranking (§1B).
- Compose-order: Changes 1b (descending decline), 1 (no-org default), and the existing explicit-org path
  all live in the one `if mm is not None:` block; 1b is position-1 so a descending+person query never
  reaches the ascending paths.

### Change 2 — Answer formatting (the narrowing nudge)
**File:** `v2/core/retrieval/structured_answer.py` (where `top_people_by_metric` is formatted).

When the answer came from a **defaulted** NJIT-wide scope, append a deterministic one-liner that
**names the scope and shows the narrowing syntax** (reviewer-recommended phrasing):

> *"This is university-wide. Want a specific college or department? Just name it (e.g. 'most cited in YWCC')."*

- **Deterministic, appended IN the formatter — NOT after LLM compose.** `top_people_by_metric` is a
  `_DETERMINISTIC_SKILL` (`structured_answer.py:96`) so its answer is sent VERBATIM — there is no LLM
  compose to append after. The nudge is concatenated **inside `format_answer`'s `top_people_by_metric`
  branch** (~`:188-206`). Note `deterministic_suffix` (`:294`) currently handles only
  `entity_card`/`research_of_person`, NOT this skill — do **not** wire the nudge there.
- Appears **only** on the defaulted case (an explicit "…at NJIT" already named the org, so no nudge).

### Plumbing — the flag
Router sets `org_defaulted=True` in the fallback Route's args (absence == False; do NOT set it on the
explicit branch). **`run()` must thread it into the returned RESULT dict** (`a.get("org_defaulted", False)`,
alongside `n`) — `format_answer` only sees the result dict, never the Route. One boolean, router → run → formatter.

## 5. What does NOT change
- No new skill; no "ask-back/clarify" route type.
- No schema or data change (data + skill already correct — proved by the "…at NJIT" runs).
- `faculty_in_department`, `people_in_org`, `org_departments` org guards untouched — the
  default is confined to the metric-ranking branch.

## 6. Test plan (TDD — write failing first)
Router unit tests (`v2/tests/` — match existing router test file):
- `who is the most cited professor` → `Route("top_people_by_metric", {org_id: <root>, metric: citations, n:1, org_defaulted:True})`
- `highest h-index professor` → same shape, metric h_index
- `top 5 most cited faculty` → n=5, defaulted root
- `who is the most cited` (no "professor"/"faculty" word, no org) → root, defaulted — confirms
  `_PERSON_INTENT` ("who is") alone satisfies the gate.
- **Scope-gate false positives (the §4 decision — MUST be covered):**
  - `most cited paper` (no org, no person/faculty word) → **`None`** (→ RAG, not a faculty ranking)
  - `top citation award` → `None`
  - `most cited study about NJIT` → `None`
- **Bug B (descending decline, Option 3):**
  - `least cited professor at NJIT` → `Route("metric_descending_unsupported", {metric: citations, …})`
    (NOT `faculty_in_department`, NOT `None`)
  - `least cited professor` (no org) → decline route — fires regardless of org
  - `professor with the fewest citations at NJIT` → decline route
  - `lowest h-index professor at NJIT` → decline route (metric h_index)
  - `fewest citations in YWCC` → decline route — wins over the explicit org too
- **Bug B gate false positives (reviewer §3 — MUST cover):**
  - `fewest citations needed to graduate` → **`None`** (policy/threshold, no person cue → RAG, NOT a decline)
  - `papers with the fewest citations` → `None` (about publications, not faculty)
  - `lowest h-index that still counts` → `None`
- **Bug B served-answer safety (RAG reviewer):** the SERVED answer for `least cited professor at NJIT`
  is the deterministic decline and **names no specific person.** With Option 3 this is guaranteed
  structurally. Pin it with: (a) `is_deterministic(result)` is True for `metric_descending_unsupported`
  (asserts it's in `_DETERMINISTIC_SKILLS`, so the LLM is never invoked); (b) a `format_answer` unit test
  that the decline string names no person and contains the metric noun + the "most {metric}" offer.
- **Root-resolution failure:** `most cited person at NJIT` with `_root_org_id` stubbed to `None` →
  `None` (never route with `org_id=None`).
- **Negative / no-regression:**
  - `most cited professor at NJIT` → still routes, `org_defaulted=False` (org explicitly named)
  - `most cited professor in YWCC` → org_id=YWCC, not root
  - `who are the professors` (faculty, no metric, no org) → **still `None`** (no metric branch; no dump)
  - `how do I cite a paper` → still `None` (no metric match — alias guard holds)
- Formatter test: defaulted result includes the nudge line (with scope + example); explicit-org result does not.

Add all of the above to `eval/questions.txt` (grow-the-suite rule).

## 7. Goals checklist (shipped / deferred — fill at PR)
- [ ] **(Bug A)** Bare metric-rank query (no org) + person/faculty cue routes to university-wide
      `top_people_by_metric`
- [ ] Scope gate requires `_FACULTY_CUE or _PERSON_INTENT` — false positives ("most cited paper/award")
      stay `None`
- [ ] Root org resolved dynamically (`_root_org_id`, `is_active=1`), not hardcoded; None → `None`
- [ ] Narrowing nudge (scope + example) appended IN `format_answer`, threaded via the result dict,
      only on the defaulted scope
- [ ] **(Bug B, Option 3)** Descending-metric + person/faculty cue at position-1 routes to a deterministic
      `metric_descending_unsupported` decline (no faculty dump, no RAG); `_DESC_DIR` kept separate from `_RANK_CUE`
- [ ] **(Bug B gate)** Descending + metric WITHOUT a person cue ("fewest citations needed to graduate",
      "papers with fewest citations") falls through to RAG, NOT declined
- [ ] **(Bug B safety)** `metric_descending_unsupported` is in `_DETERMINISTIC_SKILLS` (LLM never invoked);
      canned text names no person and bakes no coverage numbers — guaranteed structurally
- [ ] No regression on explicit-org metric queries, faculty/people branches, or non-metric "cite" uses
- [ ] Eval questions added (both bugs + the safety assertion)
- [ ] **Deferred (explicitly OUT of scope, flag at PR):** ascending metric *ranking* (a real "least
      cited" leaderboard — the decline replaces it); broader "default-to-NJIT" for non-metric
      faculty-rank queries

## 8. Reviewers
- **Senior-eng:** correctness of the root-org resolution, flag plumbing, no-regression on the
  guarded branches, efficiency (root lookup is one cheap query).
- **RAG/LLM:** is the metric+rank+no-org → university-wide default the right intent inference?
  Is the scope gate (person/faculty cue) tight enough to avoid false positives? Is the
  deterministic nudge phrasing appropriate and non-fabricating?
- Check the build against this note's **stated goals** (§7), not just diff correctness.
