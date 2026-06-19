# University-Leader Role Lookup — Design

**Status:** design (pending senior review → gated build)
**Date:** 2026-06-19

## Problem (from a live smoke test)
"who is the provost" → router returns None → semantic RAG → returns the wrong people (e.g.
"Wunmi Sadik, Vice Provost for Faculty Affairs" / "Sotirios Ziavras, Vice Provost for Graduate
Studies" scraped from department pages) instead of **John Pelesko**, the actual Provost we seeded
under `njit-administration`. Same for "who is the chancellor / general counsel / dean of students /
CFO". Only "who is the president" happens to work (Lim is the sole person on the `njit` root, caught
by `officers_in_org`).

Root cause: university leaders live in the graph (`njit` + `njit-administration`, source='dashboard',
titles like "Provost and Executive Vice President of Academic Affairs") but:
- the router's role branch (`role_in_org`) only fires when an ORG is named ("the dean **of YWCC**")
  and its `_ROLE_HEAD` vocabulary deliberately excludes president/provost/VP; and
- with no org, the query falls through to RAG, which has no notion of "the" provost vs a vice-provost.

(NOTE: the related "tell me about <cabinet member>" bug is ALREADY fixed — `entity_card` returns the
graph card (name + title + org + phone) for graph-only people; the live failure was a stale pre-restart
bot. No change needed there. This design is only the role-lookup.)

## Goal
"who is the <university leadership role>" (no org, implicitly NJIT) → a deterministic structured
answer naming the right person, with exact disambiguation (provost ≠ vice-provost).

## Design — GENERALIZE the existing `role_in_org` (NOT a new cabinet-only skill)

Principle (per maintainer): one uniform standard for ALL people via the graph. "who is the <role>
of <org>" must work identically for the **provost of NJIT**, the **dean of NCE**, and the **chair of
Physics** — same skill, same matching rule. So we EXTEND `role_in_org`, the skill that already does
this for "dean of YWCC", rather than special-casing the cabinet.

### Change 1 — `role_in_org` searches the org PLUS its administration unit
Today it's strictly org-scoped (`role_in_org` docstring: "not descendants"). Leadership for an org
lives EITHER on the org itself OR on its dedicated administration sub-unit — uniformly:
`njit → njit-administration`, `ywcc → college-administration`, `mtsm → mtsm-administration`. So
`role_in_org(org_id)` should match `has_role` on the org's node **or** its administration child
(resolve a child whose slug ends `-administration` or `administration`, or named "… Administration").
NOT all descendants — that would wrongly let "chair of NCE" match every department chair. This keeps
it precise and uniform (the provost sits on njit-administration exactly as a college dean sits on
college-administration).

### Change 2 — segment-aware exact-head match (handles compound titles, same rule for everyone)
Today it matches `^<role>\b` against each whole title. Cabinet titles are compound
("Provost and Executive Vice President…", "Senior Vice President of Student Affairs and Dean of
Students"). Split each title on `", "` / `" and "` and match if **any segment starts with the role**
(`^<role>\b`). This is a general improvement applied to all titles:
  - "Provost and Executive Vice President…" → segment "Provost" matches `provost` ✓
  - "Senior Vice Provost for Research" → no segment starts with "provost" ✗ (so vice-provosts excluded)
  - "…and Dean of Students" → segment "Dean of Students" matches `dean of students` ✓
  - "Vice President of Athletics" → no segment starts with "president" → "president" returns only Lim ✓

### Change 3 — broaden the role vocabulary (one list, used by the one route)
Add leadership heads to `_ROLE_HEAD` so the SAME `_ROLE_OF_ORG` route recognizes them for ANY org:
`president, provost, chancellor, vice president, general counsel, chief financial officer, cfo,
dean of students, athletic director, chief of staff` (kept alongside the existing dean/chair/director/…).
`cfo` is mapped to "chief financial officer" before matching.

### Change 4 — implicit org = NJIT for university-level roles
"who is the provost" names no org. When no org resolves AND the role is a university-level head
(provost/president/chancellor/general counsel/CFO/athletic director/chief of staff), default
`org_id = njit` and run the same `role_in_org`. For org-specific roles (dean/chair) with no org named
→ stay RAG (genuinely ambiguous — which college's dean?). This is the only role-type-aware bit, and it
only chooses a DEFAULT org; the matching logic is identical for all.

### Format (structured_answer)
`role_in_org` already returns (name, title, email); extend its formatter to also show phone, and to
read naturally for a single hit ("The Provost of NJIT is John Pelesko — …"). Empty → "" → RAG. No new skill.

## Out of scope / accepted
- A role that never appears as a title segment head won't resolve → falls through to RAG (deflect, not
  a wrong name). Acceptable.
- This same generalization automatically improves college/department role lookups (e.g. a dean filed
  on `college-administration` is now found by "dean of <college>"), which is the point — uniform.

## Tests
- "who is the provost" → Pelesko (only); NOT Dhawan/Gross/Clark (vice/associate provosts).
- "who is the president" → Lim (only); NOT the VPs / NJII President.
- "who is the general counsel" → Curko. "who is the dean of students" → Boger.
- "who is eligible to be provost" / process shapes → None (RAG).
- A vice-provost query stays RAG (no false structured match).
