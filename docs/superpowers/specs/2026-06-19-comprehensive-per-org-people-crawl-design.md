# Comprehensive Per-Org People Crawl — Design (foundational, task #8)

**Status:** design (pending senior review → gated build)
**Date:** 2026-06-19
**Goal:** complete coverage — every person + all their info in every NJIT org. Stop missing people
because the crawler only walks ONE page per org.

## Problem (root cause, verified)
`explore()` walks the YWCC hub's single child link per unit. For Computer Science the hub links
`cs.njit.edu/faculty` (roster only) and the crawler **never fetches `cs.njit.edu/administration`**
(Chair, Assistant to the Chair, Senior Administrative Assistant, Associate Chairs, PhD/MS Program
Directors) or `cs.njit.edu/joint-faculty`. So whole groups of PEOPLE and their ROLES are missed
(e.g. Vincent Oria is the CS Chair but is only captured as "Professor"). NJIT is inconsistent: Data
Science's hub link is `/administration-and-faculty` (complete), ECE's dept page puts "…and Chair"
in the title (complete), CS's doesn't. A per-page hardcoded approach can't keep up.

## REVISED MODEL (maintainer-chosen): one edge per (person, org), MERGE titles — no sub-orgs
Reviewer's "sub-org per admin page" was rejected as proliferation. Instead: a person keeps ONE
`has_role` edge per org, but its `attrs.titles` LIST accumulates all their roles at that org
(Oria@computer-science → `["Professor","Chair"]`). Multiple orgs still = multiple edges (Oria also
joint@informatics). The richness (bio/research/contact) stays as knowledge on the node/KB. Three
coordinated mechanics make this work:

1. **Merge, not overwrite (run-scoped):** when a second page of the SAME crawl re-appoints a person
   to an org they're already in this run, UNION the new titles into the existing edge (keep the
   first/strongest category). Track a per-run `seen (pid, org)` set: first touch this run overwrites
   (so a changed title isn't kept stale), later touches merge. Re-crawls don't accumulate stale titles.
2. **Section→role derivation on admin pages (conservative; REPORTED for manual check):** on an
   `/administration` page the role is in the SECTION head, not the title. For a section whose head
   names a single leadership role ("Chair", "Department Chair", "…Director"), add that role as a title
   to the faculty-rank people in the section (Oria, "Professor" under "Chair and Administrative
   Support" → +"Chair"); support-titled people (Assistant to the Chair) keep their own title.
   Ambiguous multi-role sections ("Associate Chairs and PhD Program Director", 3 faculty) → add the
   section's PRIMARY role to all and FLAG them. EVERY derived role is reported so the maintainer
   verifies/corrects (deflect-over-wrong: if unsure, leave the bare title and report it).
3. **M3 end-of-pass (not per-listing):** accumulate `present_by_org` + the set of listing-own orgs
   across the whole explore() call; run the section-scoped deactivation ONCE after the BFS, per
   listing-own org, using the UNION of all its feeder pages — so the faculty page can't retire the
   admin-only staff (Thompson, Butler) and vice-versa. Parent orgs (dean-reappointment targets) are
   not swept (unchanged).

CS's extra pages (`/administration`, `/joint-faculty`) are added as hub children so they land in the
SAME explore() call as `/faculty` (required for the run-scoped merge + end-of-pass M3 to union them).

## Approach: discover ALL of an org's people pages, crawl them all, merge, derive roles

### 1. Per-org page discovery (adaptive, not hardcoded)
Given an org's base host (the host of its known listing URL, e.g. `cs.njit.edu`):
- **Follow the site's own nav**: collect same-host links whose anchor text is People / Faculty /
  Administration / Staff / Directory / Leadership / Joint Faculty (case-insensitive).
- **Probe common paths** as a backstop: `/administration`, `/administration-and-faculty`,
  `/faculty`, `/joint-faculty`, `/our-people`, `/people`, `/staff`, `/leadership`.
- Keep each candidate that returns ≥1 `people.njit.edu/profile/<slug>` card (the shared template).
- If NONE found → the org is a **special case**: flag it for its own explicit entry point /
  manual seed (never emit a silent partial roster).

### 2. Crawl every discovered page as a listing; MERGE by slug
Reuse `parse_listing`; a person on `/faculty` AND `/administration` is one node (existing slug
dedup) accruing roles from each page. People appearing ONLY on `/administration` (assistants,
associate chairs, directors) are finally captured.

### 3. Role derivation per page (the hard part — scrutinize)
- **Faculty/roster pages:** title-based, as today (`category_for_section` + the title text;
  "…and Chair" → captured as a Chair role).
- **Administration pages:** the SECTION conveys the leadership role; the person's own title is
  often just "Professor". Rule per admin section:
  - Map the section head → a role: "Chair …" → `Chair`; "Associate Chair(s) …" → `Associate Chair`;
    "… Program Director(s)" / "… Directors" → `Director` (or the specific directorship in the title).
  - The role applies to people in that section whose OWN title is a **faculty rank**
    (Professor/Associate/Assistant/Distinguished/Lecturer) — they ARE the chair/director.
  - People whose own title is a **support role** (Assistant to the Chair, Administrative Assistant)
    keep their own title and are NOT assigned the section's leadership role.
  - Worked example — `cs.njit.edu/administration`, section "Chair and Administrative Support":
    Oria (Professor) → role **Chair**; Thompson ("Assistant to the Chair"), Butler ("Senior
    Administrative Assistant") → keep own titles (support).
- Open question for review: when a section lists several faculty-rank people (e.g. "Associate Chairs
  and PhD Program Director" → Ding, Koutis, Curtmola), do all get "Associate Chair", or split by the
  trailing "Program Director"? Proposal: assign the section's PRIMARY role to all faculty-rank
  members, and prefer a person's own title if it already names a specific role.

### 4. EntryPoint model
An org's anchor becomes **base host + discovered pages** (the discovery in §1). The existing
`ALL_ENTRY_POINTS` list still names the orgs; `_CHILDREN` / hub links seed the base host. Special
cases keep an explicit URL or manual seed.

### 5. Completeness verification (per the standing rule)
After a dev crawl: count people-per-org before/after; assert no anchored academic org has 0 people;
spot-check that CS now has Oria=Chair + the admin staff + joint faculty; run `verify_kg`; confirm KB
items exist for new people.

## Rollout (DFS, task #9)
Build the engine, then apply DFS: root (done, special) → offices → colleges → departments (where the
gain is largest) → personal sites. Each node gated + verified.

## Risks
- Admin-section role derivation is heuristic; wrong assignment names the wrong chair → verify on a
  dev crawl across several depts before live; prefer DEFLECT (no role) over a wrong role.
- Discovery could pull a non-org page (a center/lab listing) → scope to same-host + profile-card
  presence; accept extra orgs over missed people.
- More fetches per org → keep the polite delay; re-crawl stays idempotent (slug dedup + M3).
