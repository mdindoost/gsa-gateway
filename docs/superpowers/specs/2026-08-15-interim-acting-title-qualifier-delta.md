# Delta: role-title matching must accept `Interim` / `Acting` qualifiers

**Date:** 2026-08-15
**Status:** DELTA-SPEC — awaiting senior-eng + RAG review, then owner approval
**Scope:** two matcher sites. No new skill, no new route, no schema change.
**Trigger:** NJIT moved to interim leadership (2026-08). Landing the new roster would take
"who is the president of NJIT" from **working → broken**.

Lean delta per `feedback_reuse_prior_designs` — this mirrors an already-shipped matcher rather
than introducing a new mechanism.

---

## 1. Evidence

NJIT's senior-administration page now reads: Pelesko = "Interim President", Kam = "Interim
Provost and Executive Vice President for Academic Affairs", Bandelt = "Acting Dean in the
Newark College of Engineering".

Two identical dev DBs, differing only in the word "Interim":

| Pelesko's title | router resolves to | answer |
|---|---|---|
| `President` | org 1 (NJIT) | "New Jersey Institute of Technology has 1 officer(s): President — John Pelesko." |
| `Interim President` | org 52 (Office of the President) | "I don't have officer information for Office of the President." |

And with the post-recrawl titles simulated:

```
people_by_role('dean')    -> Bandelt's 'Acting Dean' NOT returned                                    MISS
```

> **⚠️ CORRECTION (senior-eng finding #7a).** v1 of this spec also cited
> `people_by_role('provost')` returning Kam as evidence that `interim` already works. That call
> was **UNSCOPED**; the router scopes it, and the routed path is empty for an unrelated reason:
>
> ```
> route('who is the provost of njit') -> people_by_role(role_head='provost', org_id=1)
> people_by_role(conn,'provost', 1)   -> []          # provost edge lives on org 47
> people_by_role(conn,'provost')      -> [Moshe Kam, ...]
> ```
>
> `ROLE_SCOPE_LEVEL['provost']=3` climbs to the university root (org 1), which holds only the
> President, while the provost edge sits on org 47 (`njit-administration`). So
> "who is the provost of NJIT" falls through to RAG **today and after this change**, and
> `eval/questions.txt:125` already exercises it. That is a **separate, pre-existing org-scoping
> defect** — see §4. Do not read this delta as fixing the provost query.

## 2. Root cause — the two matchers have diverged

`router.py:457` documents itself as *"reuse people_by_role's matcher"*. It does not.

| Site | Code | Strips qualifier? |
|---|---|---|
| `entity.people_by_role` (`entity.py:292-296`) | `^{role}\b` + `_scope = ^(?:departmental\|department\|university\|interim)\s+` | **`interim` yes, `acting` no** |
| `router._org_answers_title` (`router.py:461`) | `^{title}\b(?!')`, **no scope strip** | **no** |

So:
- **Bug A** — `_org_answers_title` rejects "Interim President", the president-collision swap
  never fires, and the query lands on the empty "Office of the President" org.
- **Bug B** — `_scope` lacks `acting`, so "Acting Dean" is not a dean.

Blast radius is not one person. Current NJIT leadership: Pelesko (Interim President), Kam
(Interim Provost), Bandelt (Acting Dean), Kenney (Interim Senior VP Finance), Haggerty
(Interim VP IST), Clark (Interim Chief of Staff).

## 3. Change

1. **One shared qualifier regex**, single source of truth, used by BOTH sites:
   ```python
   # A leading SCOPE or ACTING-CAPACITY word is not a rank modifier: an Interim President IS
   # the president. A RANK modifier (Vice/Associate/Assistant/Deputy) is a DIFFERENT role and
   # must still NOT match.
   ROLE_QUALIFIER_RE = re.compile(r"^(?:departmental|department|university|interim|acting)\s+", re.I)
   ```
2. `entity.people_by_role` uses it (adds `acting`; `interim` behavior unchanged).
3. `router._org_answers_title` uses it (currently strips nothing) — fixes Bug A.

**Deliberately NOT added:** `vice`, `associate`, `assistant`, `deputy`, `senior`. An Associate
Dean is not the Dean; matching those would be a correctness regression, and the existing
docstrings call this out explicitly.

### 3.1 G5 — the qualifier must survive to the user (added after RAG review)

`people_by_role` / `officers_in_org` / `role_in_org` are **not** in `_DETERMINISTIC_SKILLS`
(`structured_answer.py:165-187`), so their answers go through `compose_from_rows`. A temp-0.0
rephrase of "Interim President — John Pelesko" into "John Pelesko is the President of NJIT"
would be exactly the attribute-drop the anti-fabrication clauses exist to prevent — and it
would reintroduce the permanence implication this change is supposed to avoid.

Precedent: `title_of_person` is deterministic for this exact reason — *"the (affiliated)/(joint
appointment) marker on a title is load-bearing — the LLM must not reword it away."* `Interim` /
`Acting` is the same class of marker.

**Measured before deciding** (2026-08-15, `llama3.1:8b`, temp 0.0, 3 trials each):

```
[KEPT] who is the president of NJIT -> "...is an interim position held by John Pelesko"
[KEPT] who is the dean of NCE       -> "The current acting dean ... is Matthew Bandelt"
```

6/6 preserved. So **no compose change and no warmth reversal is needed** — making these skills
verbatim would reverse the owner's 2026-07-02 WS3 greeting decision (the blocker documented in
`project_deterministic_rosters_fix`), and that is not warranted by the evidence.

Instead G5 is enforced as a **pinned regression test**: the composed answer must still contain
`Interim` / `Acting`. Under the LLM-agnostic hard line, a pass on one local model is not proof
for the next one, so the guard belongs in the suite rather than in a one-off observation.

## 4. Non-goals

- Not touching `_bare_officer_office`, `_longest_non_officer_office`, or the collision-swap
  control flow — only the predicate that decides whether an org can answer a title.
- Not changing the roster data (separate, already staged) or the crawler.
- Not adding a "former officeholder" concept.

### Known limits — DEFERRED, loudly, not dropped (senior-eng #1/#2/#3/#7a)

1. **`_officer_org` terminal dead-end.** When the bare-officer office can answer nothing AND
   gate 3 fails, `officers_in_org(52)` emits a **terminal** "I don't have officer information
   for Office of the President" with no RAG fall-through. So bare **"who is the president"**
   (no org) is broken today and stays broken — `alt is None`, so the office org survives. The
   principled fix is for `_officer_org` to return `(None, None)` in that case, but that changes
   an asserted behavior in `v2/tests/test_router_org_role_collision.py:113` (`== 52`) and needs
   owner sign-off. **Rejected alternative:** relaxing gate 3 to "alt has ANY admin edge" —
   verified to break `test_router_org_role_collision.py:108` ("who is the ywcc president" must
   not dump the YWCC roster labeled as officers), which is exactly what gate 3 exists to stop.
2. **`Interim Co-Chair` does not match `chair`.** A co-chair arguably is a chair; `co-` is not
   in the qualifier alternation. Recorded as a judgment call, not an oversight.
3. **The provost org-scoping defect** (see the §1 correction): `ROLE_SCOPE_LEVEL['provost']`
   climbs to org 1 while the edge sits on org 47. Unrelated to qualifiers; needs its own fix.
4. **Terse query-side qualifier**: `njit interim president` → no route (`interim` survives as
   residue and is blocked like `former`). Adding it to `_TERSE_OFFICER_STOP` is a real judgment
   call since `former` is blocked deliberately — not changed silently.
5. **`Acting Program Coordinator` matches `program coordinator`.** The strip cannot distinguish
   capacity-"Acting" from discipline-"Acting" (HCAD/theater is live-crawled). Pinned by a test
   so the behavior is on record.
- **Stale KB PROSE is out of scope for this matcher change and is NOT fixed by it** (RAG review
  change 2 — stated so the recrawl is not silently assumed). Measured on live: **53 active
  `njit_www_crawl` rows** still assert the old leadership, including id 32870
  (`www.njit.edu/president/presidents-cabinet`) which carries the entire former cabinet verbatim,
  plus crawler rows 11824/11825 ("Dean of the Newark College of Engineering" for Kam) and the
  orphaned dashboard bio 3997 ("Teik C. Lim is the President of NJIT"). Semantic RAG will keep
  serving those regardless of this fix. **This delta MUST land together with:**
  `scripts/run_explore.py` (Kam/Bandelt), `scripts/crawl_www.py --entry president` (the cabinet
  page), an explicit deactivate of item 3997, and `v2/scripts/embed_all.py`.

  Note: 3997 is orphaned — its `metadata.entity_id` is `manual/teik-c-lim` while Lim's person
  keys are `dashboard/njit-administration/teik-c-lim` and `dashboard/njit/teik-c-lim`, so
  `remove_person_role`'s bio-retirement clause silently no-ops. That mismatch is a separate
  latent defect (any person retired this way keeps a live bio) — recorded, not fixed here.

## 5. Tests (TDD)

1. `_org_answers_title(njit, "president")` is True when the only title is `Interim President`;
   still False when it is `Vice President` or `President's Advisory Council`.
2. End-to-end: "who is the president of NJIT" resolves to org 1 and names Pelesko, with the
   title stored as `Interim President`.
3. `people_by_role("dean")` returns a person titled `Acting Dean`.
4. `people_by_role("dean")` still EXCLUDES `Associate Dean` / `Assistant Dean` / `Vice Dean`
   — the anti-regression guard.
5. `people_by_role("provost")` still excludes `Vice Provost` / `Associate Provost`
   (unchanged behavior, pinned so the shared regex can't loosen it).
6. "who is the dean of Newark College of Engineering" names Bandelt once his Acting Dean title
   is in place.

## 6. Risk

| Risk | Mitigation |
|---|---|
| Loosening the matcher lets a rank modifier through | Only two literals added to an existing allowlist; tests 4 + 5 pin the exclusions both ways |
| `_org_answers_title` swap now fires where it didn't | That is the fix; test 1 pins both the positive and the two negatives |
| Divergence returns later | The regex becomes one exported constant used by both call sites |

**Backout:** revert the commit + `scripts/restart.sh`. No data or schema change.

## 7. Goals checklist

| Goal | Status |
|---|---|
| G1 `Interim <role>` matches `<role>` at both sites | ✅ + the **third** qualifier-sensitive site the reviewer found (`_SUPPORT_LEAD`, entity.py) |
| G2 `Acting <role>` matches `<role>` | ✅ incl. multi-token `Acting Interim Dean` (repeating strip) |
| G3 `Vice`/`Associate`/`Assistant`/`Deputy` still do NOT match | ✅ 9 parametrized negatives, incl. `Acting Associate Dean` |
| G4 the two matchers stop diverging | ✅ **strengthened** — v1 shared only the regex, which the reviewer showed left 3 divergences. Now ONE `entity.title_head_matches()` (segment split + qualifier strip + `(?!')` + support-lead skip) called by both, plus the missing `p.is_active` filter on `_org_answers_title` |
| G5 qualifier survives to the user | ✅ pinned by test; measured 6/6 KEPT on `llama3.1:8b` |

**Tests:** 28 in `v2/tests/test_role_qualifier.py` (unit + E2E incl. the leader rule and the
inactive-person gate). Full run of the affected suites: 84 passed, 3 failed — all 3 confirmed
pre-existing by reverting this change and re-running (identical failures).

**Verified end-to-end** on a dev copy carrying the staged interim roster:

| query | before | after |
|---|---|---|
| who is the president of NJIT | "I don't have officer information for Office of the President." | "…1 officer(s): **Interim President — John Pelesko**." |
| who is the dean of NCE | Moshe Kam (stale) | "Matthew Bandelt — **Acting Dean**" |
| who runs NJIT | — | "John Pelesko — **Interim President**" |
| who leads NCE | **missed** (`_scope` lacked `acting`) | "Matthew Bandelt — **Acting Dean**" |
| who is the provost of NJIT | empty → RAG | empty → RAG (**unchanged** — deferred item 3) |
