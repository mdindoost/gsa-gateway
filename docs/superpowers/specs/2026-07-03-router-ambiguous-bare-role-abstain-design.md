# Router F — abstain-hint for genuinely-ambiguous bare role/officer fragments (design)

**Date:** 2026-07-03
**Author:** Kavosh maintenance (Mohammad Dindoost, owner) — design decisions delegated to and
ruled by Fable (binding, per `feedback_delegate_opinions_to_fable`).
**Status:** Fable APPROVED (design) → senior-eng + RAG reviews folded → Fable adjudicated (binding, all
findings accepted, RAG #1 scoped to officer-branch-only) → TDD build.
**Scope:** `v2/core/retrieval/router.py` (a new terminal branch) + `v2/core/retrieval/structured_answer.py`
(the static `ambiguous_officers` deflection — `run()` + `format_answer()` + `_DETERMINISTIC_SKILLS`) +
`v2/core/retrieval/unified_router.py` (extend `_FASTPATH_CUE` so the live path reaches `route()` for the
full officer catch-set — senior-eng B1) + `eval/questions.txt`.
**Workstream:** short-query correctness + follow-up (thread F). A shipped (`d7ef41f`), D+E shipped
(`96af18c`). B (remove v1 LLM expander) follows, independent.

---

## 1. Problem

The deterministic router (`route()`) correctly returns `None` for a bare, org-less role/officer
fragment — it does NOT misroute it. But `None` means the query flows downstream to the
slot-extractor → RAG → **live njit.edu fallback**, and for genuinely-ambiguous fragments that path
produces a **confident-wrong** answer.

**Verified live (the founding failure):** bare **"officers"** →
`route()`=None → past the WS4 abstention gate → **live fallback** → returns **AFROTC Detachment 490**
recruiting content (`source_note=https://rotc.njit.edu/`). Confident, and completely irrelevant to
what a grad student means by "officers".

The gap: a genuinely-ambiguous bare term slips past BOTH the router (correctly abstains from routing)
AND the WS4 answerability gate (does not fire) into a wrong answer. Per the workstream's guiding
principle — **resolve intent deterministically; never guess; a confident-wrong answer is the
dangerous failure** — such a fragment should ABSTAIN with a helpful hint to name the org, not answer.

### What is (and isn't) in scope

`route()`=None today for: `officers`, `officer`, `who are the officers`, `treasurer`, `secretary`,
`vp`, `e-board`, `executive board`, `dean`, `chair`, `director`, `coordinator`, `chancellor`, `fund`,
`money` (all verified). F targets the **role/officer** subset of these. Non-role bare nouns
(`fund`, `money`) are OUT — the ledger measured `money → Bursar` via RAG as acceptable, and detecting
"fund is ambiguous" deterministically would require a curated semantic word-list (the rejected
alias-table pattern). F adds NO curated list.

---

## 2. Design

### Behavior: abstain + hint (owner's choice)

NOT clarify-with-resume. When a bare role/officer fragment is genuinely ambiguous, the bot answers
with a helpful nudge to name the org. No follow-up state, no PendingAction, not resumable.

### Placement: one branch at the very END of `route()`, immediately before the final `return None`
(`router.py:756`).

The end-of-function placement IS the safety argument. Every confident branch — D's collision swap,
the officer-identity branch (`:674`), the role-lookup branch (`:643-666`, including the explicit
person-cue path that answers "who is the provost" / "who is my dean"), E's terse-officer branch
(`:674-684`), metrics, links, person branches — has already had its chance and declined. F therefore
catches ONLY queries that fall to `None` today, making regression on any currently-routed query
**structurally impossible**.

### The gate: shared conjuncts + a branch-asymmetric org test, two-way dispatch

**Dispatch first** on which regex the query matches — role-vocab or officer-title — then apply that
branch's gate. (Dispatch is role-vocab-first; the residue check strips ONLY the dispatch regex, so
the two branches are mutually exclusive — see below.)

**Shared conjuncts (both branches):**
1. the branch's regex (`_ROLE_VOCAB_RX` for branch 1, `_OFFICER_TITLE_RX` for branch 2) matches, **and**
2. no process cue (`_OFFICER_PROCESS`) is present, **and**
3. after stripping the **dispatch regex** span (and, for branch 2, the resolved org phrase — see its
   org test), **zero non-stopword residue** remains under `_F_STOP`.

**Branch-1 (role) org test — strict `org is None`:**
`_find_org(conn, q)` → `(None, None)`. Every bare-office slug resolves an org and is therefore
excluded from branch 1: `provost` → org 53, `registrar` → org 24, `dean of students` → org 20,
`vice provost`/`associate provost` → their offices (all verified). These stay in D's territory
(`None` → RAG today), unchanged. **`njit dean` (root) also stays `None`→RAG** — branch 1 never
extends to the root; the role branch genuinely *has* deans via the subtree, so a root scope is a real
answer, not an ambiguity. Branch 1 owns only genuinely **org-less** role fragments.

**Branch-2 (officer) org test — `org is None` OR (`org is root` AND `not _has_true_officers(org)`):**
the root-org clause is the RAG-review fix for the verified `njit officers` / `officers at njit` →
AFROTC sibling of the founding bug. Those resolve `(1, 'njit')`; `_has_true_officers(root)` is
`False` (verified), so the clause fires. Because an org phrase IS present here, branch 2's residue
check also strips the resolved `org_phrase` (mirrors E's terse-officer branch) so `njit officers` →
strip `njit`+`officers` → zero residue → fires. **This clause is officer-specific by design**
(`_has_true_officers` is an officer-answerability fact, meaningless for roles) — it is NOT applied to
branch 1. Bare-office slugs `president`/`vice president` → org 52 (not root) → still excluded.

**Dispatch results:**

- **Branch 1 — role-vocab match** (`dean`, `chair`, `director`, `coordinator`, `associate dean`,
  `executive director`, `cfo`→`chief financial officer`, …) →
  **`Route("people_by_role", {"role_head": _ROLE_SYNONYM.get(w, w), "org_id": None})`**.
  Reuses the SHIPPED skill and its data-driven output (structured_answer.py:229-243): three-way on
  holder count — >25 → "N hold X, narrow by org — e.g. …"; 2–25 → "N hold X: [listed]"; ==1 →
  "Name — Title (Org)."; 0 → "" → RAG. **This output is LLM-COMPOSED** (`people_by_role` is NOT in
  `_DETERMINISTIC_SKILLS`) — grounded + anti-fab-rephrased at temp 0, identical to what "who is the
  &lt;role&gt;" already produces. Same Facts, same compose path; not verbatim.
- **Branch 2 — officer-title match** (`officers`, `officer`, `treasurer`, `secretary`, `vp`,
  `e-board`, `executive board`; president/vice president excluded by the org test) →
  **`Route("ambiguous_officers", {})`** — a NEW terminal deflection with **no DB query**, wired at
  THREE sites (`run()` → `format_answer()` → `_DETERMINISTIC_SKILLS`), mirroring
  `metric_descending_unsupported` exactly. Its hint is **verbatim** (deterministic-skill → no
  compose). Static hint, non-GSA example first:
  > *"I'm not sure which organization you mean — try naming it, e.g. "GWICS officers" or "GSA
  > officers"."*

**Mutual exclusion (F4 — correctness-load-bearing):** the residue check strips ONLY the **dispatch**
regex (`_ROLE_VOCAB_RX.sub` for branch 1, else `_OFFICER_TITLE_RX.sub` for branch 2), never both. So a
query with BOTH a role token AND an officer token (e.g. "director secretary") leaves the other token
as non-stopword residue → conjunct 3 fails → no fire. Stripping both would wrongly fire it. A test
asserts a bare token never satisfies both branches.

### Why the split is principled (open-world completeness + GSA-equal)

- **Branch 1 enumerates because academic-title coverage is crawler-COMPLETE.** `_ROLE_VOCAB`
  deliberately excludes club titles (president/treasurer/secretary — router.py:92-94) and holds only
  dense academic titles no club holds, all gathered by the crawler. So `people_by_role(role, None)`
  listing real orgs is an HONEST total, and it **cannot surface a GSA/club row** — verified live for
  every role-vocab word. GSA-neutrality here is **structural for routing**; result-set neutrality is
  data-contingent and enforced by the CI 1-row-never-GSA assertion (§5), run against the live DB.
- **Branch 2 is static because club-officer data is manually PARTIAL.** Officer/deprep roles live on
  exactly 5 orgs today (GSA / GWICS / Grad BME Society / Iranian Cultural Assoc / Sanskar), maintained
  by hand (gsanjit.com is Wix / non-crawlable; only some clubs ingested). Enumerating "the orgs with
  officers" would make a **false-completeness claim** — many NJIT clubs with officers aren't in the
  KG. A static "e.g. …" honestly signals "name *your* org" without implying the list is exhaustive.
  (And `officers`/`e-board` have no title head → routing them to `people_by_role` → empty → RAG → the
  AFROTC garbage.) The non-GSA-first example ordering keeps the optics GSA-equal — no ranking implied.

### The F residue stop-set

`_F_STOP = _TERSE_OFFICER_STOP ∪ {"who", "who's", "whos", "is", "are", "my", "our"}`.
Includes `who`/`is`/`are` so **"who are the officers"** (a verified AFROTC failure) → residue empty →
fires. **Excludes `what`/`how`/`why`/`when`/`where`** so **"what is a dean"** keeps `what` as
non-stopword residue → does NOT fire → RAG (definitional ask). "who [is/are] the X" is a
person-identity ask (F's target); "what is a X" is definitional (RAG's).

### The confidence gate, restated

There is no score. The "confidence gate" IS the structural conjuncts plus the positional fact that F
runs last: every deterministic branch already declined (the confidence check in a scoreless router),
an ambiguity-bearing token is present, the branch's org test holds (no org, or root-with-no-officers
for branch 2), no process cue, and nothing else is in the query. Binary, provable, each conjunct
independently unit-testable. No probabilistic threshold — a threshold would be a score-shaped guess.

---

## 3. Verified catch-sets (live DB, 2026-07-03)

**Branch 2 — static `ambiguous_officers` deflection (org-less OR root-with-no-officers officer words):**
`officers` · `officer` · `who are the officers` · `treasurer` · `secretary` · `vp` · `e-board` ·
`executive board` · **`njit officers` (root, no true officers)** · **`officers at njit` (root)**.

**Branch 2 — MUST NOT fire (resolve a NON-root org → D's territory, `None`→RAG unchanged):**
`president` (org 52) · `vice president` (org 52) · `ywcc officers` (college-scoped — D's territory;
build MUST verify it does not itself hit AFROTC-class garbage, else that's a logged D follow-up).

**Branch 1 — `people_by_role(role, None)` (org-less role words, ≥1 holder):**
`dean` (18) · `chair` (21) · `director` (51→narrow-hint) · `coordinator` (6) · `associate dean` (24)
· `assistant dean` (1) · `associate chair` (4) · `general counsel` (1) · `chief financial
officer`/`cfo` (1) · `athletic director`/`director of athletics` (1) · `chief of staff` (3) ·
`executive director` (9) · `associate director` (20) · `assistant director` (22). Every one verified
to surface NO GSA/club row.

**Branch 1 — empty → RAG (accept):** `chancellor` (0 holders). Tolerable — RAG, not officer-garbage;
matches the role branch's existing empty→RAG. Build MUST verify live that zero-holder role words
don't hit a bad live-fallback answer.

**OUT of F via conjunct 2 (org-resolve → deferred to D, unchanged):**
`president` · `vice president` · `provost` · `registrar` · `dean of students` · `vice provost` ·
`associate provost`.

**Branch 1 — MUST NOT fire on root (branch-1 org test stays strict `org is None`):**
`njit dean` → `(1,'njit')` → NOT org-less → branch 1 skips → `None`→RAG unchanged. The root-org
clause is officer-branch-only; the role branch genuinely has deans via subtree, so root is a real
scope, not an ambiguity.

**MUST NOT fire — already routed by an earlier branch:**
`who is the provost` (role branch, person-cue) · `who is my dean` (role branch) · `who is the
director` (role branch) · `GSA officers` (E) · `dean of YWCC` (role branch) · `njit president` (D).

**MUST NOT fire — residue/process cue → RAG:**
`what is a provost` · `president office hours` · `dean's list requirements` · `how to impeach the
president` (process) · `officer training program` · `money` / `fund` (no role/officer token).

---

## 4. Non-goals / explicitly deferred (flagged, not dropped)

- **Role-office-collision words stay deferred to D.** `provost`/`registrar`/`vice provost`/`dean of
  students` resolve their office org → conjunct 2 excludes them → they keep today's `None`→RAG. This
  is exactly where the D+E spec left `provost` (data on org 47, subtree-scope issue). Extending F to
  rescue them overlaps D's territory for a rare query class (YAGNI). **Deferred watch (D follow-up,
  not F):** bare `president`/`vice president` resolve org 52 and fall `None`→RAG today; if live
  probing shows RAG/live returns garbage for them (it did NOT for these — the AFROTC case was the
  org-less "officers"), that is a **D** extension, logged, not folded into F.
- **`chancellor` (0 holders) → RAG.** Accepted; harmless (not officer-garbage). Not force-deflected.
  Branch 1 has NO static floor — a role word that drops to 0 holders (e.g. a departure zeroes the
  1-holder `general counsel`/`cfo`/`athletic director`) silently reopens the empty→RAG→live path. A
  standing zero-holder eval probe (§5) catches such a data change; branch-1 safety is data-conditional
  (branch-2's static deflection is not).
- **Non-role bare nouns (`fund`, `money`, `aid`, `deadline`)** stay with RAG — no curated word-list.
- **`NJIT officers` / `officers at NJIT` — NOW SHIPPED** (root-org clause on the branch-2 org test);
  the founding "officers → AFROTC" goal is met for the org-less AND the root form. Non-root college
  officer asks (`ywcc officers`) remain D's territory (logged watch, §3).
- **Not resumable.** Thread A's PendingAction machinery must NOT register F's deflection — confirm
  `resumable_action()` (structured_answer.py:506) excludes `ambiguous_officers` by default; assert
  it in a test.

---

## 5. Testing

TDD. Unit tests against `route()`'s return — `is None` or the exact `Route(skill, args)` — **not**
"→ RAG" (per SE finding: under `ROUTER_V21=1` a `None` first hits the LLM slot-extractor; that
downstream behavior is out of this change's control).

**Branch 2 fires** → `Route("ambiguous_officers", {})`: `officers`, `officer`, `who are the officers`,
`treasurer`, `secretary`, `vp`, `e-board`, `executive board`, **`njit officers`** (root clause),
**`officers at njit`** (root clause).
**Branch 1 fires** → `Route("people_by_role", {"role_head": w, "org_id": None})`: `dean`/`chair`/
`coordinator`/`director`; `cfo` → `role_head="chief financial officer"` (synonym) (assert route only —
answer text is the shipped skill's).
**Branch-1 stays strict `org is None`:** `njit dean` → **`None`** (root ≠ org-less for roles; must NOT
fire branch 1). This is the key non-regression the branch-asymmetric org test buys.
**Org-test exclusions (no F fire — stay as today):** `president`, `vice president` (org 52, non-root),
`provost`, `registrar`, `dean of students`.
**Already-routed (F must not intercept):** `who is the provost` → `people_by_role('provost', 1)`;
`who is my dean` → `people_by_role('dean', None)`; `GSA officers` → `officers_in_org(gsa)`; `dean of
YWCC` → role branch; `njit president` → D.
**Residue/process guards (→ None):** `what is a dean` (non-stopword `what`), `president office hours`,
`dean's list requirements`, `officer training program`, `how to impeach the president` (process cue),
`money`, `fund`.
**Mutual-exclusion test (F4):** `director secretary` → **None** (strip dispatch regex only → the other
token is non-stopword residue → no fire); assert no bare token satisfies both branch predicates.
**`ambiguous_officers` answer-layer tests (F2 — the three wiring sites):** `run()` returns the skill
dict; `format_answer()` returns the exact static hint string; `ambiguous_officers ∈ _DETERMINISTIC_SKILLS`
(so `is_deterministic(result)` is True → no compose); `resumable_action()` returns None for it. Guard
against the terminal `return ""` regression (empty → live-fallback = the bug it kills).
**Live-path integration test (senior-eng B1 — THE real fix, not just `route()` unit tests):** under
`ROUTER_V21=1`, drive `UnifiedRouter.decide()` on EACH branch-2 word (`officers`, `treasurer`,
`secretary`, `vp`, `executive board`, `njit officers`) and assert it reaches KG → `ambiguous_officers`
(i.e. `_FASTPATH_CUE` now covers them, or they classify KG). Without this the cue-set can silently
regress and re-open the gap.
**Data-audit tests (Fable's must-nail, live-DB fixture):** for every `_ROLE_VOCAB` word, the 1-row
confident branch (structured_answer.py:239-241) never fires with a club/GSA org in the org column
(assert — so a future GSA "director"/"coordinator" can't silently become a GSA-default); document each
word's holder count incl. the zero-holder `chancellor`. These are live-DB integration checks (51
directors / 18 deans / 0 chancellors are live facts), not toy-DB unit tests.

**Eval additions** (`eval/questions.txt`, per `feedback_grow_correctness_suite`):
- **Branch-2 fires — expect DEFLECT:** `officers` · `who are the officers` · `treasurer` · `secretary`
  · `njit officers` · `officers at njit`.
- **Branch-1 fires — expect a real ANSWER (NOT deflect — F5 correction):** `dean` · `chair` ·
  `director` · `coordinator`.
- **Zero-holder standing probe (RAG #2):** `chancellor` (guards against a role word silently turning
  into a live-fallback answer on a data change).
- **Must-not / deferred probes:** `president` (deferred-to-D, must-not-AFROTC) · `njit dean` (must
  stay a role answer / None, not branch-2) · `provost` (org-resolve) · `what is a dean` · `president
  office hours` · `money`.
Buttons (👍/👎/🔄) on the hint per `feedback_structured_no_buttons` — confirm deflections already get
them via the normal answer path.

---

## 6. Goals checklist (shipped vs deferred)

| Goal | Status |
|---|---|
| Kill the "officers → AFROTC" confident-wrong path (org-less AND root forms) | **ship — FULLY** (branch 2 + root-org clause covers `officers`/`who are the officers`/`njit officers`/`officers at njit`) |
| Bare officer words (`treasurer`/`secretary`/`vp`/`e-board`) → abstain-hint | **ship** (branch 2) |
| …reach `route()` on the LIVE path (not just unit tests) | **ship** (`_FASTPATH_CUE` extended + live-path integration test — senior-eng B1) |
| Bare role words (`dean`/`chair`/`director`/`coordinator`) → data-driven narrow-by-org answer | **ship** (branch 1 reuses `people_by_role`; LLM-composed, not verbatim) |
| GSA-equal | **ship** — structural for ROUTING (vocab excludes club titles; deflection never queries); result-set neutrality is data-contingent, enforced by the CI 1-row-never-GSA assertion against live DB |
| Residue/process/definitional forms → RAG | **ship** (conjunct 3 + `what` excluded; dispatch-regex-only strip) |
| No regression on currently-routed queries | **ship** (end-of-route placement; branch-1 org test stays strict `org is None` → `njit dean` unchanged) |
| Role-office-collision words (`provost`/`registrar`) | **deferred** — resolve an org, stay in D's territory (as D+E scoped) |
| Bare `president`/`vice president` (org 52) | **deferred** — D follow-up if live shows garbage |
| Non-root college officer asks (`ywcc officers`) | **deferred/watch** — D's territory; build verifies it isn't itself AFROTC-garbage |
| `chancellor` (0 holders) & 1-holder roles | **accepted** → RAG; standing zero-holder eval probe guards data drift |

---

## 7. Guiding-principle compliance

- **Deterministic routing, no LLM in the router** — pure rule-based; no model call. `ambiguous_officers`
  is deterministic/verbatim (in `_DETERMINISTIC_SKILLS`, no compose); branch-1 reuses the existing
  `people_by_role` skill, whose answer IS LLM-composed (grounded + anti-fab, temp 0) — identical to the
  shipped "who is the &lt;role&gt;" path, not a new model call in the router. ✓
- **Never guess; a confident-wrong answer is the dangerous failure** — F converts the org-less (and
  root-with-no-officers) ambiguous officer fragments — the exact class that produced AFROTC — into an
  honest abstain-hint, and bare role fragments into a data-driven narrow-by-org answer; every fire path
  is guarded, residue/process/definitional forms fall to RAG. ✓
- **GSA-equal, no bias table** — no curated word-list. Branch 1 is GSA-neutral for routing (vocab
  excludes club titles) with the 1-row-never-GSA CI assertion as the result-set backstop; branch 2 is
  static (open-world completeness — club data is manually partial, so enumerating would falsely imply a
  total) and leads with a NON-GSA example (`GWICS` before `GSA`), never showing GSA alone. ✓
- **Bare-office slugs owned by D, not F** — the single `_find_org → None` conjunct defers president/
  provost/registrar uniformly. ✓
- **No flag** — a pure narrowing of the `None` fall-through; backout = revert one commit. ✓
