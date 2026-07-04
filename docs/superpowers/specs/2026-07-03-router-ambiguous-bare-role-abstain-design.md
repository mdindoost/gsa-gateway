# Router F — abstain-hint for genuinely-ambiguous bare role/officer fragments (design)

**Date:** 2026-07-03
**Author:** Kavosh maintenance (Mohammad Dindoost, owner) — design decisions delegated to and
ruled by Fable (binding, per `feedback_delegate_opinions_to_fable`).
**Status:** Fable APPROVED → pending senior-eng + RAG review → owner spec review → TDD build
**Scope:** one file — `v2/core/retrieval/router.py` (a new terminal branch) + one small handler in
`v2/core/retrieval/structured_answer.py` (the static `ambiguous_officers` deflection) +
`eval/questions.txt`.
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

### One uniform gate, two-way dispatch

The gate is a single three-conjunct check (NOT two independent per-branch checks):

1. an `_OFFICER_TITLE_RX` **or** `_ROLE_VOCAB_RX` token is present in `q`, **and**
2. `_find_org(conn, q)` → **`(None, None)`** (no org resolved), **and**
3. after stripping the matched token, **zero non-stopword residue** remains **and** no process cue
   (`_OFFICER_PROCESS`) is present.

Conjunct 2 is the load-bearing mechanism: **every bare-office slug resolves an org and is therefore
excluded from F.** `president`/`vice president` → org 52, `provost` → org 53, `registrar` → org 24,
`dean of students` → org 20, `vice provost`/`associate provost` → their offices (all verified). These
stay in D's territory (`None` → RAG today), unchanged. F owns only genuinely **org-less** fragments.

Dispatch on which regex matched:

- **Role-vocab match** (`_ROLE_VOCAB_RX`: `dean`, `chair`, `director`, `coordinator`, `associate
  dean`, `executive director`, `cfo`→`chief financial officer`, …) →
  **`Route("people_by_role", {"role_head": _ROLE_SYNONYM.get(w, w), "org_id": None})`**.
  Reuses the SHIPPED skill + its data-driven hint verbatim (structured_answer.py:229-243), which is
  three-way on holder count: >25 → "N hold X, narrow by org — e.g. …"; 2–25 → "N hold X: [listed]";
  ==1 → confident "Name — Title (Org)."; 0 → "" → RAG. Byte-identical to what "who is the &lt;role&gt;"
  already produces — the consistency goal, zero new hint text.
- **Officer-title match** (`_OFFICER_TITLE_RX`: `officers`, `officer`, `treasurer`, `secretary`,
  `vp`, `e-board`, `executive board`; president/vice president excluded by conjunct 2) →
  **`Route("ambiguous_officers", {})`** — a NEW terminal deflection handled in `structured_answer.py`
  with **no DB query**, added to the deterministic-skill set (so the hint is never LLM-composed;
  mirrors `metric_descending_unsupported`). Static hint:
  > *"I'm not sure which organization you mean — try naming it, e.g. "GSA officers" or "GWICS
  > officers"."*

Precedence between the two: a query that contained BOTH a role-vocab token AND an officer-title token
(e.g. "director secretary") would leave the second token as non-stopword residue after stripping the
first → **conjunct 3 fails → no fire**. So no single org-less query reaches both branches; the zero-
residue guard makes the dispatch order immaterial. The build dispatches role-vocab first, then
officer-title, and a test asserts a bare token never matches both branches.

### Why the split is principled (GSA-equal by construction)

- `_ROLE_VOCAB` **deliberately excludes** club titles (president/treasurer/secretary — router.py:92-94)
  and contains only dense academic titles no club holds. So branch 1 → `people_by_role(role, None)`
  **cannot surface a GSA/club row** — verified live for every role-vocab word. GSA-neutral by
  construction, and the holder-count logic self-selects: a role spread across many orgs becomes a
  narrow-by-org hint; a unique role becomes a direct answer (not a hardcoded default — nothing to be
  ambiguous against).
- Officer-title words are the collision-prone/sparse/club-scoped set. Routing them to
  `people_by_role` would either read as "these are the only presidents" or, for a GSA-only treasurer,
  fire the 1-row branch as a silent GSA-default. And `officers`/`e-board` have no title head →
  empty → RAG → the AFROTC garbage. So branch 2 is a static deflection that never queries.

### The F residue stop-set

`_F_STOP = _TERSE_OFFICER_STOP ∪ {"who", "who's", "whos", "is", "are", "my", "our"}`.
Includes `who`/`is`/`are` so **"who are the officers"** (a verified AFROTC failure) → residue empty →
fires. **Excludes `what`/`how`/`why`/`when`/`where`** so **"what is a dean"** keeps `what` as
non-stopword residue → does NOT fire → RAG (definitional ask). "who [is/are] the X" is a
person-identity ask (F's target); "what is a X" is definitional (RAG's).

### The confidence gate, restated

There is no score. The "confidence gate" IS the three structural conjuncts plus the positional fact
that F runs last: every deterministic branch already declined (the confidence check in a scoreless
router), an ambiguity-bearing token is present, no org anchors it, and nothing else is in the query.
Binary, provable, each conjunct independently unit-testable. No probabilistic threshold — a threshold
would be a score-shaped guess.

---

## 3. Verified catch-sets (live DB, 2026-07-03)

**Branch 2 — static `ambiguous_officers` deflection (org-less officer words):**
`officers` · `officer` · `who are the officers` · `treasurer` · `secretary` · `vp` · `e-board` ·
`executive board`.

**Branch 2 — MUST NOT fire (resolve an org → D's territory, `None`→RAG unchanged):**
`president` (org 52) · `vice president` (org 52).

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
- **Non-role bare nouns (`fund`, `money`, `aid`, `deadline`)** stay with RAG — no curated word-list.
- **`NJIT officers` / `officers at NJIT`** — `_find_org` resolves the university root, so conjunct 2
  fails → still `None`→RAG (not org-less). F v1 does not cover org-resolved officer asks. Documented;
  if live probing shows garbage, a follow-up may extend conjunct 2 to
  `org is None OR (org is root AND not _has_true_officers)`. NOT built now.
- **Not resumable.** Thread A's PendingAction machinery must NOT register F's deflection — confirm
  `resumable_action()` (structured_answer.py:506) excludes `ambiguous_officers` by default; assert
  it in a test.

---

## 5. Testing

TDD. Unit tests against `route()`'s return — `is None` or the exact `Route(skill, args)` — **not**
"→ RAG" (per SE finding: under `ROUTER_V21=1` a `None` first hits the LLM slot-extractor; that
downstream behavior is out of this change's control).

**Branch 2 fires:** each of `officers`, `officer`, `who are the officers`, `treasurer`, `secretary`,
`vp`, `e-board`, `executive board` → `Route("ambiguous_officers", {})`.
**Branch 1 fires:** `dean`/`chair`/`coordinator` → `Route("people_by_role", {"role_head": w,
"org_id": None})`; `cfo` → `role_head="chief financial officer"` (synonym); `director` likewise
(assert route only — answer text is the shipped skill's).
**Conjunct-2 exclusions (no F fire — stay as today):** `president`, `vice president`, `provost`,
`registrar`, `dean of students`.
**Already-routed (F must not intercept):** `who is the provost` → `people_by_role('provost', 1)`;
`who is my dean` → `people_by_role('dean', None)`; `GSA officers` → `officers_in_org(gsa)`; `dean of
YWCC` → role branch; `njit president` → D.
**Residue/process guards (→ None):** `what is a dean` (non-stopword `what`), `president office hours`,
`dean's list requirements`, `officer training program`, `how to impeach the president` (process cue),
`money`, `fund`.
**Answer-layer test:** `ambiguous_officers` in `is_deterministic` set (no compose); its formatted
text is the exact static hint; `resumable_action()` returns None for it.
**Data-audit tests (Fable's must-nail):** for every `_ROLE_VOCAB` word, the 1-row confident branch
(structured_answer.py:239-241) never fires with a club/GSA org in the org column (assert, so a future
data change — e.g. a GSA "director" — can't silently turn bare "director" into a GSA-default); and
document each word's holder count incl. the zero-holder `chancellor`.
**ROUTER_V21 parity:** F behaves identically under `ROUTER_V21=1` in shadow and flipped modes — the
UnifiedRouter KG family invokes `route()` and treats `ambiguous_officers` as terminal (distinct call
sites message_handler.py:293 vs :479).

**Eval additions** (`eval/questions.txt`, per `feedback_grow_correctness_suite`) — both fire and
must-not probes: `officers` · `who are the officers` · `treasurer` · `secretary` · `dean` · `chair` ·
`director` · `coordinator` · `president` (deferred-to-D, must-not-AFROTC) · `provost` (org-resolve) ·
`what is a dean` · `president office hours` · `money`. Each F fire must classify as **deflect** in
`scripts/eval.sh` (verify the classifier keys off the deflection shape). Buttons (👍/👎/🔄) on the
hint per `feedback_structured_no_buttons` — confirm deflections already get them via the normal path.

---

## 6. Goals checklist (shipped vs deferred)

| Goal | Status |
|---|---|
| Kill the "officers → AFROTC" confident-wrong path | **ship** (branch 2 static deflection) |
| Bare officer words (`treasurer`/`secretary`/`vp`/`e-board`) → abstain-hint | **ship** (branch 2) |
| Bare role words (`dean`/`chair`/`director`/`coordinator`) → data-driven narrow-by-org answer | **ship** (branch 1 reuses `people_by_role`) |
| GSA-equal (no club row surfaced, no GSA default) | **ship** (structural — vocab excludes club titles; deflection never queries) |
| Residue/process/definitional forms → RAG | **ship** (conjunct 3 + `what` excluded) |
| No regression on currently-routed queries | **ship** (end-of-route placement) |
| Role-office-collision words (`provost`/`registrar`) | **deferred** — resolve an org, stay in D's territory (as D+E scoped) |
| Bare `president`/`vice president` (org 52) | **deferred** — D follow-up if live shows garbage |
| Org-resolved officer asks (`NJIT officers`) | **deferred** — conjunct-2 extension, not built |
| `chancellor` (0 holders) | **accepted** → RAG |

---

## 7. Guiding-principle compliance

- **Deterministic routing, no LLM in the router** — pure rule-based; no model call. `ambiguous_officers`
  is deterministic (no compose); branch-1 reuses the existing deterministic skill. ✓
- **Never guess; a confident-wrong answer is the dangerous failure** — F converts the org-less
  ambiguous fragments (the class that produced AFROTC) into an honest abstain-hint or a data-driven
  narrow-by-org answer; every fire path is guarded, residue/process/definitional forms fall to RAG. ✓
- **GSA-equal, no bias table** — no curated word-list; branch 1 is GSA-neutral by construction;
  branch-2 examples pair GSA with a non-GSA club (GWICS) and never show GSA alone. ✓
- **Bare-office slugs owned by D, not F** — the single `_find_org → None` conjunct defers president/
  provost/registrar uniformly. ✓
- **No flag** — a pure narrowing of the `None` fall-through; backout = revert one commit. ✓
