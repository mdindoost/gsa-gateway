# Non-home role attribution — org-scoped role lookup must ignore joint/affiliated edges (2026-08-26)

**Status:** REV 3 — BOTH reviews folded. Senior-eng: APPROVE-WITH-CHANGES (10 findings).
RAG/retrieval: APPROVE-WITH-CHANGES (14 findings, one of which changed the design — see Part A
org-agnostic mode). Owner: *"do not wait on me, do your job to fix the problem"* (2026-08-26) →
cleared to build.
**Trigger:** owner, 2026-08-26 — *"each department has obviously one chair. so why when I asked chair
of informatics you replied two chair."*
**Related:** `2026-07-05-affiliated-faculty-category-design.md` (shipped the `joint`/`affiliated`
tiers + markers). This spec closes that spec's explicitly-deferred item
*"people_by_role/role_in_org/people_in_org — still traverse the demoted edge unmarked"*, which has
now surfaced as a user-visible wrong answer.

## Problem — reproduced live

```
$ bash scripts/ask.sh "who is the chair of informatics" --answer
  → KG skill=people_by_role args={'role_head': 'chair', 'org_id': 7}
  structured answer:
    2 hold a "chair" title in Informatics: Michael Halper — Chair (Informatics); Vincent Oria — Chair (Informatics).
  FINAL ANSWER: "The chairs of Informatics are held by Michael Halper and Vincent Oria."
```

**Michael Halper is the Informatics chair. Vincent Oria is the Chair of Computer Science** — his own
KB profile (item 64) reads *"Professor of Computer Science and Chair of the Computer Science
Department"*. He holds only a **joint appointment** in Informatics.

The two edges (live DB, `gsa_gateway.db`):

| person | org | `category` | `source_section` | `attrs.titles` |
|---|---|---|---|---|
| Halper, Michael | Informatics | `admin` | **`Chair`** | `["Chair"]` |
| Oria, Vincent | **Informatics** | **`joint`** | **`Joint Appointments`** | `["Chair"]` |
| Oria, Vincent | Computer Science | `faculty` | `Professors` | `["Chair"]` |

## Root cause — two layers, only one of them ours to fix here

1. **Producer (NOT a bug; leave alone).** NJIT listing pages render each person's *own* title on their
   card regardless of which section the card sits in. Oria's card in the Informatics **"Joint
   Appointments"** section shows "Chair" — his CS title. `explore.py:215` → `project_appointment`
   stores `titles=p.titles` per listing appearance, faithfully. Per the hard line *crawl is
   data-bringing only*, the crawler is right to store what the page says. The section IS recorded
   (`source_section='Joint Appointments'`, `category='joint'`) — the signal is not lost.
2. **Serving (THE bug).** `entity.people_by_role` (`v2/core/retrieval/entity.py:317`) matches on the
   **title string only** and never reads `e.category`:
   ```sql
   SELECT p.name, e.attrs, p.attrs, o.name FROM edges e JOIN ... 
   WHERE e.type='has_role' AND e.is_active=1 AND p.is_active=1
     [AND json_extract(o.attrs,'$.org_id')=?]        -- org scope, no category filter
   ```
   So a non-home cross-listing lends its **home-org title to the wrong org**. `role_in_org`
   (`entity.py:351`) is a thin wrapper over it and inherits the bug.

**Semantic statement of the invariant this spec adds:** a title carried on a `joint`/`affiliated`
edge describes a role the person holds **at their home org**, not at the org the edge points to. It
may therefore never answer *"who is the &lt;role&gt; of &lt;that org&gt;?"*.

## Blast radius — measured against the live DB, not estimated

Script: iterate every active `has_role` edge, apply the production matcher
`entity.title_head_matches` for role heads {chair, dean, director, provost, president, head,
coordinator, associate chair, vice president}, group by (org, role).

**Only 6 active non-home edges carry a role title at all.** Every one is a duplicate of a title the
same person already carries on a home edge — so the fix removes false rows and loses no person.

Five orgs currently answer with a phantom second office-holder (**all `category='joint'`**):

| org | correct holder (home edge) | phantom (joint edge) | phantom's real org |
|---|---|---|---|
| **Informatics** | Halper, Michael (`admin`) | **Oria, Vincent** | Computer Science |
| Computer Science | Oria, Vincent (`faculty`) | **Geller, James** | Data Science |
| Civil & Environmental Eng | Marhaba, Taha (`staff`) | **Axe, Lisa** | NCE |
| Mechanical & Industrial Eng | Ji, Zhiming (`faculty`) | **Lieber, Samuel** | NCE / SAET |
| Mathematical Sciences | Michalopoulou, Zoi-Heleni (`admin`) | **Golowasch, Jorge** | Biological Sciences |

Sixth edge — the only row where filtering removes the **last** answer for an (org, role) pair:

- `("Biomedical Engineering", "provost")` → Kam, Moshe, `joint`, *"Interim Provost and Executive Vice
  President of Academic Affairs"*. After the fix that pair returns 0 rows → the caller falls through
  to RAG. **Correct outcome — BME has no provost.**
  ⚠️ **This pair is reachable only at the SKILL level, never through the router** (senior-eng #4,
  verified independently): `ROLE_SCOPE_LEVEL['provost']=3` makes the org climb to the `njit` root, so
  both *"who is the provost"* and *"who is the provost of biomedical engineering"* route to
  `people_by_role('provost', org_id=1)` — which returns empty **today, before any change**. Do not
  describe this as a user-visible behaviour change; it is a skill-contract change.
  No collateral loss, asserted where it is actually true — at the skill level:
  `people_by_role('provost', org_id=None)` goes **2 rows → 1 row, same single person** (Kam), because
  his `faculty@Electrical & Computer Engineering` edge carries the identical title.

**Category census (active `has_role`, live):** faculty 657 · staff 263 · admin 89 · *(null)* 75 ·
joint 65 · emeritus 57 · officer 16 · advisor 9 · **affiliated 0**.
⚠️ `affiliated` shows **2 edges total, 0 active** — the 2026-07-05 relabel of 14 edges **has fully
reverted via re-crawl**, exactly as that spec's deferred *producer-durability* item predicted. The
fix must still name `affiliated` so it is correct when the producer fix lands, but today the live
win comes entirely from `joint`.

## Design

### Part A — `people_by_role` excludes non-home edges (the fix)

In `v2/core/retrieval/entity.py`, next to the existing `_CATEGORY_MARKER`:

```python
# A title on a joint/affiliated edge is the person's HOME-org title, copied onto their card by the
# listing page they were cross-listed on. It describes a role held ELSEWHERE and must never answer
# "who is the <role> of <this org>".
# DEFINED INDEPENDENTLY of _CATEGORY_MARKER on purpose: that dict answers "how do we DISPLAY a
# non-home appointment", this set answers "which edges may ANSWER a role query". They coincide
# today (an invariant test asserts it) but deriving one from the other means a future display-only
# marker — say emeritus — would silently delete 57 emeritus edges from every role answer.
NON_HOME_CATEGORIES = frozenset({"joint", "affiliated"})
```
(Senior-eng #6. The invariant test `NON_HOME_CATEGORIES == set(_CATEGORY_MARKER)` records the
equality as a today-fact, not a definition — if someone breaks it deliberately, the test tells them
to think about which of the two they meant.)

and in `people_by_role`, add to the base SQL (both modes — see rationale):

```sql
AND (e.category IS NULL OR e.category NOT IN ('joint','affiliated'))
```

`IS NULL` is **required**: 75 active edges have a NULL category (Makerspace staff, postdocs — see
census) and they are ordinary home appointments. A bare `NOT IN` drops NULLs in SQL three-valued
logic. Measured effect today (senior-eng #1/#2): the bare form drops **140** edges (65 joint + 75
NULL) vs **65** for the guarded form; of the 75 NULL edges only **2** currently carry a title that
`title_head_matches` any role head (Bruno + Heck, `Director, …` @ Hillier College). So the live cost
of getting this wrong is 2 wrong answers today — but the NULL population **grows with every new
section header** (`category_for_section` returns `None` for any unrecognized section,
`v2/core/ingestion/discovery.py:41-45`), so the guard is mandatory, not cosmetic.

Bind the placeholders from `sorted(NON_HOME_CATEGORIES)` — frozenset iteration order is not stable
across runs, and an unstable placeholder order defeats SQLite's statement cache.

`role_in_org` needs **no change** — it delegates and inherits the fix.

**The two modes get DIFFERENT rules — this changed in rev 3 (RAG #3, HIGH).**

| mode | rule | why |
|---|---|---|
| **org-scoped** (`org_id` given) | **hard exclusion** of non-home edges | the question is *"who is the &lt;role&gt; **of this org**"*. A borrowed title cannot answer it under any circumstance — keeping it marked would still assert "the provost of BME is Kam", which is false. Empty → RAG is the safe direction. |
| **org-agnostic** (`org_id is None`) | drop a non-home row **only if the same person already has a home row for the same `role_head`**; otherwise KEEP it, org rendered through `_org_label` → `Informatics (joint appointment)` | the question is *"who is the &lt;role&gt;"* — an org-agnostic answer must not lose a real office-holder |

**Why not a blanket filter in org-agnostic mode (the rev-1/rev-2 design):** rev 1 justified it with
*"all 6 non-home role edges duplicate a home edge (measured)"*. True today, but that is an accident
of the current crawl, not an invariant. **A person whose ONLY active edge is non-home exists live
right now** — `Fjermestad, Jerry` (`joint@Informatics`, sole active edge). His title carries no role
head so there is no impact today, but the class is real: a home listing page that fails to crawl, or
gets reconciled away while the cross-listing survives, produces exactly this state. Under a blanket
filter, *"who is the dean"* would then **silently omit a real dean** with nothing to detect it. The
home-aware rule kills the duplication (the actual stated goal) without opening a recall hole.

What this achieves on today's data: identical to the blanket filter (every one of the 6 non-home
role edges has a home counterpart, verified per person), so `provost` still goes 2 rows → 1 and
`chair` 31 → 26 — but the future failure mode is a *marked* row instead of a missing one.

**Rejected alternatives**
- *Mark instead of drop* (`Oria — Chair (Informatics (joint appointment))`): the marker does not
  repair the falsehood. The claim "Oria is a chair in Informatics" is simply untrue; a parenthetical
  does not make a wrong roster right, and the composer may drop it. Reject.
- *Filter only when `org_id is not None`*: leaves the duplicate-row bug and creates two rules. Reject.
- *Prefer home edges, fall back to non-home when empty*: reintroduces the falsehood in exactly the
  case where it is least detectable (nothing else to contradict it). Reject.
- *Fix the crawler to not store the borrowed title*: violates the crawl-is-data-bringing-only hard
  line (the page really does show that title on that card) and would discard information the serving
  layer legitimately uses elsewhere. Reject.
- *Producer-side: route the `Joint Appointments` section to `None`* (senior-eng #10). The lever
  exists — `section_policy.route(...)` already returns `None` for rolled-up/cross-listed sections and
  the loop `continue`s (`explore.py:186-188`), so the edge would simply never be created. Reject on
  a stronger ground than the hard line: it would **destroy the joint membership itself**, which
  `people_in_org`, `entity_card`, `title_of_person` and FacultyFolio all legitimately use. The
  membership is real; only the borrowed *title's scope* is wrong. Fix the scope, keep the fact.

### Part B — `people_in_org` title marker (SHIP — senior-eng #8 resolved the open question)

`skills.people_in_org` (`skills.py:237`) correctly **includes** joint/affiliated people (they *are*
in the org) but renders the borrowed title unmarked, so *"who works in Informatics"* lists
`Chair — Vincent Oria`. Fix: mark the title → `Chair (joint appointment) — Vincent Oria`.

⚠️ **Do NOT use `_org_label` here** (senior-eng #8): that helper labels the **org**, and
`people_in_org` returns `(name, title, email)` with no org column — the org lives in the lead-in.
Render from `_CATEGORY_MARKER` directly via a small shared helper `_title_with_marker(title,
category, title_is_category)` so the literal string stays single-sourced. The literal
`"(joint appointment)"` is **load-bearing** — `_compose_preserves_facts` greps for it — so the test
must assert the exact string, not merely "a marker is present".
- `officers_in_org` is **already safe** (filters `category IN ('officer','deprep','admin')`).
- `faculty_in_department` is **already safe** (filters `category='faculty'`).
- ✅ **Compose risk resolved — the marker IS protected.** `_compose_preserves_facts`
  (`bot/core/message_handler.py:188-222`) runs the marker check **before and independent of** the
  counted-roster gate, count-aware, over ALL Facts:
  ```python
  for marker in ("(affiliated)", "(joint appointment)"):
      if cf_comp.count(marker) < cf_facts.count(marker):
          return False
  ```
  A dropped marker reverts the answer to verbatim Facts. Additionally `people_in_org` renders
  `f"{org} has {len(rows)} people: …"`, which matches `_A4A_ROSTER_LEADIN_RX`, so these rosters
  already run the full tail-token survival check (at Informatics' 57 people they almost certainly
  already revert to verbatim). Part B costs essentially nothing.
- Live impact scope (joint/affiliated edges per org): Computer Science 11 · Data Science 10 ·
  Biomedical Eng 10 · Informatics 9 · MIE 6 · Math Sci 5 · CEE 5 · CME 5 · ECE 4.
- Part B remains **separable** — Part A stands alone if it ever needs to be reverted independently.

### Part D — close the compose guard on `people_by_role` itself (RAG #5, MEDIUM — NEW in rev 3)

`_A4A_ROSTER_LEADIN_RX` (`bot/core/message_handler.py:177`) is
`\b\d+\s+(?:faculty|people|officers?|persons?|departments?)\b`. It does **not** match
`people_by_role`'s 2–25-row lead-in `2 hold a "chair" title in Informatics: …` — only its `>25`
branch (`31 people hold …`) matches, via the word "people". So **multi-row role answers are
unguarded against compose truncation today**: the composer can silently drop a name from a 5-chair
roster and nothing reverts it. This fix reduces the exposure (fewer multi-row answers) but does not
close it, and it is the same `[[feedback_no_bandaid_align_data_and_retrieval]]` pattern — fix both
sides in one change.

**Fix, part 1 (one line):** add the `hold` lead-in to the alternation →
`\b\d+\s+(?:faculty|people|officers?|persons?|departments?|hold)\b`. No other skill emits a
`<digits> hold` lead-in, so over-triggering is nil.

⚠️ **BUILD-TIME CORRECTION (2026-08-26): the one-line fix alone does NOT work — measured, not
argued.** The review (and rev 3 of this spec) claimed the tail-token check would then catch a
dropped row because the tail token is `Chair`. It is not caught, and `Chair` is exactly why. The
item check strips the parenthetical (which holds the ORG) and then tests only `toks[-1]`; for
`people_by_role`'s **name-first** row shape `Michael Halper — Chair (Informatics)` the last token is
the **title word every sibling row shares**, so a dropped row false-passes on the surviving row's
identical title. Demonstrated with a red test before the fix:
```
facts   = '2 hold a "chair" title in NCE: Lisa Axe — Chair (ChemE); Zhiming Ji — Chair (MIE).'
dropped = '2 hold a "chair" title in NCE: Lisa Axe — Chair.'
_compose_preserves_facts(facts, dropped)  ->  True     # WRONG: Ji vanished
```
**Fix, part 2:** the item check tests **both edge tokens** (`{toks[0], toks[-1]}`), because the two
roster shapes put the distinctive token at opposite ends — title-first rows (`officers_in_org`:
`Chair — Michael Halper`) end in the name, name-first rows (`people_by_role`) start with it. Cost of
the extra strictness: an over-trigger only keeps the complete Facts **verbatim**, which is the
documented safe direction of this guard. Locked by test 16.

### Part C — `_primary_role` must mark the borrowed org (senior-eng #3, HIGH — NEW in rev 2)

The completeness sweep found a call site rev 1 missed. `entity._primary_role` (`entity.py:88-100`)
picks the lowest-`_ROLE_RANK` edge and returns `titles[0]` plus the **raw** `oname` — it does not
call `_org_label`, unlike its two siblings `title_of_person` (`entity.py:465`) and `entity_card`
(`entity.py:582`), which both do. And `_ROLE_RANK` ranks `joint: 3` **above** `staff: 4`,
`advisor: 5`, `emeritus: 6`, `affiliated: 7` and above NULL (`.get(cat, 50)`), so a joint edge wins
the "primary role" for anyone whose other edges are staff/advisor/emeritus.

Live leakage today (2 rows):
```
Jerry Fjermestad   cats=['joint']          -> ('Professor of MIS', 'Informatics')
Amir Miri          cats=['staff','joint']  -> ('Associate Professor', 'Mechanical & Industrial Engineering')
```
`_primary_role` feeds `resolve_people` → `person_disambig`, which is in `_DETERMINISTIC_SKILLS` and
renders **verbatim** — so a borrowed org reaches the user unmarked inside a "which did you mean?"
roster. Neither row carries a leadership title, so this is not the reported bug, but it is the same
class and rev 1 wrongly claimed the call-site set was closed.

**Fix (one line, additive, matches the two siblings):**
```python
org = _org_label(oname, cat, title_is_category=not titles)
```
Note `_primary_role` DOES take the org label (it displays an org), so unlike Part B `_org_label` is
the right helper here.

**Also unlisted in rev 1, left alone deliberately:** `dashboard/app.js:394` (`edit_title` reads an
arbitrary `LIMIT 1` has_role edge, unmarked). LOW — admin-only UI, the operator is editing the row
they are looking at. Recorded, not fixed.

### Explicitly OUT OF SCOPE — the second sub-case (flagged loud, not silently dropped)

A **college-level title landing on a `faculty` home edge** is the same class of error but is *not*
fixed by a category filter, because the edge is a genuine home appointment:

- *"chair of New Jersey School of Architecture"* → **Cohen, Maurie** (Chair of Humanities & Social
  Sciences) + **Riether, Gernot** — both `category='faculty'`, `source_section='Architecture Faculty'`.
- *"provost of Electrical & Computer Engineering"* → **Kam, Moshe** (the university's Interim Provost).
- *"chair of Newark College of Engineering"* → all **6** department chairs, `admin` /
  `source_section='Department Chairs'` — arguably correct-as-filed, but reads wrong.

Fixing these needs section-aware or home-org-aware attribution (does `source_section` name the role,
or is the org the title's actual scope?) — a larger design. **Not attempted here.**

**Shipping the narrow fix IS defensible, with measured evidence (RAG #8):** after Part A, **no
DEPARTMENT returns more than one chair** — the owner's complaint is fully closed. The residual
`>1 chair` orgs are exactly three, all *above* department level, and the follow-up spec's blast
radius is therefore already measured:

| org | type | chairs | filed as | verdict |
|---|---|---|---|---|
| Newark College of Engineering | college | 6 | `admin` / `Department Chairs` | correct-as-filed (they ARE its dept chairs) |
| Hillier College of Architecture & Design | college | 2 | `admin` / `Leadership` | correct-as-filed |
| New Jersey School of Architecture | school | 2 | `faculty` / `Architecture Faculty` | **genuinely wrong** — Cohen is Chair of Humanities |

So the residual is **1 wrong org**, not a broad inconsistency, and it is not reachable by any
department-chair question.

**UPDATE 2026-08-26 (post-ship) — NJSOA verified against NJIT and PARTLY closed.** Owner asked to
confirm the facts before fixing. Checked `design.njit.edu/our-people` + the three profiles:
- **Riether IS the NJSOA chair** ("Chair", Department of Architecture) — our answer was RIGHT.
  Schwartz likewise chairs Art + Design.
- **Cohen was the only error**: his profile reads "Chair … Chair of the Department of Humanities and
  Social Sciences" plus **"Joint appointment … the Hillier College of Architecture and Design"**, yet
  the crawler filed his NJSOA edge as `category='faculty'` (a HOME appointment) off the "Architecture
  Faculty" section.
- **Key parsing insight for any follow-up:** NJIT profile headings render `<role>, <division the
  person sits in>` — NOT the unit chaired. Esperdy's *"Dean Architecture, Provost & Academic
  Affairs"* is the tell, and it is why Riether and Schwartz BOTH show *"Chair, Hillier College of
  Architect & Design"*. A card alone can never say which unit a bare "Chair" scopes to.
- **Fixed** by `scripts/_hcad_chair_scope_fix.py` (gated, backed up, idempotent): Cohen's NJSOA edge
  `faculty` → `joint`. The shipped Part A then excludes it automatically. Live: NJSOA chair → Riether
  only; HSS chair → Cohen (preserved); Cohen still in the NJSOA roster marked "(joint appointment)"
  and correctly OUT of `faculty_in_department`.
- **Still open, deliberately:** "who is the chair of Hillier College" returns Riether + Schwartz. HCAD
  is a COLLEGE (its Dean is Esperdy); both chair SCHOOLS inside it. Re-filing those two `admin@hcad`
  Leadership edges onto njsoa/art-design was designed and CUT — both already hold a `faculty` edge on
  the destination, so the merge would flip it to `admin` and silently drop them from
  `faculty_in_department`. Needs the college-level design, not a lossy edge move.
- ⚠️ **The data fix is NOT durable**: `run_explore.py` re-creates Cohen's `faculty@njsoa` edge from
  the Architecture Faculty section. Re-run the script after each crawl until the producer-side
  home-appointment cap (deferred in the 2026-07-05 affiliated spec) lands.

### Decisions recorded (asked and answered during review — do NOT relitigate)

- **`people_by_role` does NOT join `_DETERMINISTIC_SKILLS`** (RAG #7). Single-row compose was
  measured clean and greeting-compatible (*"The current chair of Informatics at NJIT is Michael
  Halper. You can reach him at michael.halper@njit.edu."*), and making it deterministic would reverse
  the owner's 2026-07-02 warmth decision — the same blocker that parked
  [[project_deterministic_rosters_fix]] — for no measured gain. Part D guards the multi-row case
  instead.
  *(Noted, not fixed: compose adds the word "current", an attribute not in the Facts — a mild breach
  of the `compose_from_rows` "don't attach an unlisted attribute" clause. Pre-existing, not a blocker.)*
- **The officer-collision path needs no change** (RAG #11): `_org_answers_title` scans
  `category='admin'` and `_has_true_officers` scans `officer`/`deprep`, and the handoff target
  `officers_in_org` filters `officer/deprep/admin` — all three already exclude non-home categories.
  Add a one-line comment there pointing at `NON_HOME_CATEGORIES` so a future editor does not write a
  fourth category rule.
- **`title_of_person` stays as it is** (RAG #12): it will still render Oria as
  `Chair — Informatics (joint appointment)` while `people_by_role(chair, Informatics)` returns only
  Halper. That is **coherent precisely because the marker is there** — "he holds a Chair title, and
  his Informatics appointment is joint" is true; "he is the chair of Informatics" is not. Pinned by a
  test so nobody later "harmonizes" the two.
- **Pre-existing artifact, not a regression** (RAG #14): RAG answers on the provost questions begin
  `"ID 32910 (Office of the Provost),"` — a context-block artifact leaking into the answer. Unrelated
  to this change; it will appear in the verification run. File separately.

## Tests (TDD — written before the change)

New, in `v2/tests/` alongside the affiliated-tier tests:
1. `people_by_role(role_head='chair', org_id=<informatics>)` on a fixture with Halper(`admin`) +
   Oria(`joint`) → **exactly one row**, Halper. *(The reported bug, locked.)*
2. Org-agnostic `people_by_role('chair')` with a person holding home+joint edges of the same title →
   the person appears **once**, attributed to the home org.
3. **NULL-category guard:** an edge with `category IS NULL` carrying a role title is **kept**.
   *(Locks the 75-edge regression the naive `NOT IN` would cause.)*
4. Each of `officer`/`deprep`/`admin`/`faculty`/`staff`/`emeritus`/`advisor` is kept.
5. Empty-after-filter → `[]`, so `format_answer` returns `""` and the caller falls through to RAG
   (the BME/provost case) — never a fabricated or a stale answer.
6. `role_in_org` inherits the filter (same fixture, wrapper asserted).
7. Part B: `people_in_org` marks a joint title with the **exact** literal `"(joint appointment)"`
   (the string `_compose_preserves_facts` greps for) and leaves a home title bare.
8. **Router-level end-to-end** (senior-eng #9 — tests 1-6 all bypass the router, but the repro is a
   router query): `route(conn, "who is the chair of informatics")` → `run` → `format_answer` yields
   exactly one name.
9. **Empty-then-RAG at the CALLER**, not just `format_answer() == ""`: assert
   `_structured_from_route` returns `None` on a filtered-empty result (test 5 covers only half the
   fall-through).
10. **Only-joint person stays excluded, and that is intentional**: `Fjermestad, Jerry` is the sole
    active person whose entire edge set is `joint`. Lock it so a later "fall back to non-home when
    empty" refactor trips the suite.
11. **Invariant**: `NON_HOME_CATEGORIES == set(_CATEGORY_MARKER)` (see Part A — a today-fact, not a
    definition).
12. **Parametrize test 4** over `set(_ROLE_RANK) - NON_HOME_CATEGORIES` plus `None`, so a newly
    added category is covered by construction rather than by a hand-listed seven.
13. **Org-agnostic count-branch boundary** (senior-eng #7): assert the org-agnostic `chair` answer
    stays on the `len(rows) > 25` count-hint branch (31 → 26 rows; the cutoff is 25).
14. **Part C**: `_primary_role` on a joint-only person returns the org marked
    `"Informatics (joint appointment)"`, and an ordinary `faculty` person's org stays bare.
15. **Org-agnostic recall hole (the rev-3 design change)**: a person whose ONLY edge is
    `joint` and whose title DOES carry the role head is **kept** by the org-agnostic call, with the
    org marked `"X (joint appointment)"` — and is still **excluded** by the org-scoped call for X.
    This is the test that would have failed under the rev-1/rev-2 blanket filter.
16. **Part D**: `_compose_preserves_facts` returns False when a composed answer drops a name from a
    multi-row `people_by_role` Facts string (`2 hold a "chair" title in …: A — …; B — …`), i.e. the
    new `hold` lead-in actually engages the tail-token check.
17. **A3 follow-up** (RAG #10): `person_names_of` on the Informatics chair result returns exactly ONE
    name, so `context_rewrite.ambiguity_clarify` no longer CLARIFYs on *"what is his email"*.
18. **RAG #12 pin**: `title_of_person(Oria)` still renders `Informatics (joint appointment)` while
    `people_by_role('chair', informatics)` returns only Halper — assert BOTH in one test.

Regression: the existing affiliated-tier suite + `officers_in_org`/`faculty_in_department` tests must
stay green.

## Verification (post-build, before merge)

- `bash scripts/ask.sh "who is the chair of informatics" --answer` → Halper only.
- The same for the other four orgs in the blast-radius table.
- **Skill-level** (NOT via `ask.sh` — see the BME note above): `people_by_role('provost', None)`
  returns exactly one row, Kam. This replaces rev 1's *"`who is the provost` → still Kam"* check,
  which **fails against current code before any change** (senior-eng #5): that query routes to
  `people_by_role('provost', org_id=1)` on the `njit` root, where Kam holds no edge, so the
  structured layer already returns empty and RAG answers. A verification step that cannot pass is
  worse than none.
- `bash scripts/ask.sh "who is the provost" --answer` → **unchanged from today** (RAG answers).
  Recorded as a no-delta check, not as a win.
- `bash scripts/eval.sh` as a regression gate (`--min-answered 90 --min-correct 80`).
- Per [[feedback_grow_correctness_suite]], **add to `eval/questions.txt`**: `who is the chair of
  informatics` · `who is the chair of computer science` · `who is the chair of mathematical sciences`
  · `who is the chair of mechanical and industrial engineering` · `who is the chair of civil and
  environmental engineering` · `who is the provost of biomedical engineering` (must not fabricate a
  provost) · `who works in informatics` (Part B marker). **`who is the provost` is deliberately NOT
  added** — it is a RAG answer both before and after, so it would encode a false expectation in the
  regression gate (senior-eng #5/#9).

## Risk / rollback

- **Code-only.** No DB write, no migration, no re-embed, no `.env` change. Restart required (code).
- ⚠️ **Format-branch boundary** (senior-eng #7): the org-agnostic renderer flips to the
  *"too many to name — narrow it by org"* hint at `len(rows) > 25` (`structured_answer.py:325`).
  Measured before → after: `chair` 31 → **26** · `dean` 16 → 16 · `director` 43 → 43 ·
  `provost` 2 → 1 · `president` 6 → 6. Distinct **people** counts are unchanged for every head
  (independent confirmation of Goal 4). `chair` now sits **one edge above the cutoff** — the answer
  shape does not change today, but one more retired chair flips it from a count-hint to a 26-name
  roster. Test 13 locks the current branch.
- **Rollback** = revert the commit + `bash scripts/restart.sh`.
- Worst case if the rule is wrong for future data (org-scoped mode only — org-agnostic now keeps the
  row, marked): an (org, role) pair answerable only via a non-home edge returns empty → RAG/live
  answers instead. That is a **fall-through, not a KG fabrication**.
  ⚠️ **Do not overclaim this** (RAG #2, measured): the fall-through is *not* an abstain and Gate-2
  does *not* reliably catch it. Simulated on the real pipeline, *"who is the provost of biomedical
  engineering"* falls through to RAG and returns a fabricated org attribution
  (*"…Dr. Trina Arinzeh …in Biomedical Engineering"*) with `abstain=False` — and the BEFORE run is no
  better (*"Dr. Tara Alvarez is the Chair of Informatics in the Newark College of Engineering…"*).
  This is **pre-existing RAG behaviour, unchanged by this fix**, but the rollback argument must not
  rest on "RAG is safe". The real safety argument is narrower and still holds: we stop the **KG**
  from asserting a false office-holder, and the KG is the layer users trust as authoritative.
- Detection if the producer later starts writing genuine roles onto joint edges: test 5 plus the eval
  questions above.

## Goals checklist (per [[feedback_review_against_plan]])

| # | Goal | Status |
|---|---|---|
| 1 | *"chair of informatics"* returns exactly one chair (Halper) | Part A |
| 2 | The same class of error fixed for the other 4 affected orgs | Part A (one rule, measured) |
| 3 | Org-agnostic role lookup stops double-listing a joint-appointed person **without losing a role-holder whose only edge is non-home** | Part A (home-aware rule, rev 3). ⚠️ Restated honestly: for `chair` the effect is a count change 31→26 and nothing user-visible — 26 still trips the `>25` count-hint branch and the org list is byte-identical. Only observable for small role sets (`provost` 2→1). |
| 4 | No person/answer lost — verified against the live DB, not assumed | Part A + measurement above |
| 5 | NULL-category edges (75 live) unaffected | Part A + test 3 |
| 6 | Non-home appointments still *visible* in org rosters, honestly marked | Part B — **SHIP** (both reviews: the compose guard genuinely protects the marker) |
| 7 | College-level-title-on-home-edge sub-case (NJSOA, ECE, NCE) | **OUT OF SCOPE — flagged, needs its own design** |
| 8 | `affiliated` tier covered even though 0 edges are live today | Part A (set-driven, not literal) |
| 9 | Verification questions added to `eval/questions.txt` | Verification section (rev-3: the un-passable `who is the provost` check removed, 4 shapes added) |
| 10 | `_primary_role` stops leaking a borrowed org into the deterministic `person_disambig` roster | **Part C** (NEW rev 2 — senior-eng #3) |
| 11 | Multi-row role answers become guarded against compose truncation | **Part D** (NEW rev 3 — RAG #5) |
| 12 | The A3 antecedent guard stops dead-ending on a chair follow-up (2 names → 1, so *"what is his email"* resolves instead of CLARIFYing) | Part A side-effect, claimed + pinned by test 17 (RAG #10) |
