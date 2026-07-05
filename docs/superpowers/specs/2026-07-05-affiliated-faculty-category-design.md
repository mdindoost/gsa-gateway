# Affiliated-faculty category + duplicate-home correction — delta design (2026-07-05)

## Problem
14 faculty have **two** active `has_role`/`category='faculty'` (home) edges to distinct orgs — a
violation of the home-appointment-only rule ([[feedback_home_appointment_only]]). Concretely,
`faculty_in_department` (skills.py:207) filters `category='faculty'`, so a cross-listed person leaks
into the wrong roster: **"who are MTSM faculty" wrongly returns Guiling Wang + Ali Akansu**, etc.
Root cause (investigation `project_multi_home_faculty_bug`): `project_appointment` adds one edge per
listing appearance additively with no home-dedup; a person listed under ordinary faculty sections on
two dept pages gets two home edges. This spec fixes the **live data** and adds an honest
`affiliated` tier. Producer fix (crawler) is **explicitly deferred** (owner, 2026-07-05).

## Appointment model (three tiers — owner-confirmed)
| category | meaning | in "who are X faculty"? | count cap |
|---|---|---|---|
| `faculty` | **home** department (the person's real dept) | ✅ | **exactly one** |
| `joint` | a **formal** joint appointment (separate real role) | ❌ | any |
| `affiliated` (NEW) | cross-listing / courtesy (listed elsewhere, not home) | ❌ | any |

Only the **home** count is capped at one. `joint` is untouched (65 edges verified genuine, e.g.
Bader CS-joint/DataScience-home). Two `faculty` edges is always the bug.

## Resolution rule (deterministic, DB-only, auditable) — SCOPED (senior-eng CRITICAL-1)
**SCOPE FIRST to people with ≥2 active `faculty` edges** (the multi-home set). WITHIN that set only:
**reclassify to `affiliated` any active `faculty` edge whose org is NOT among the orgs the person's
active `knowledge_items` are filed under (KB-home).** Keep `is_active=1` (relabel, not delete).
- **Why the scope is mandatory (not global):** applied globally the rule flags 42 edges, not 14 —
  it would demote 28 *single-home* people whose KB prose is filed under a different org_id than their
  faculty edge (the HCAD host-vs-people split — e.g. 23 NJSOA/Art+Design faculty) and empty 5 Theater
  faculty who have 0 KB items (the graph-only branch `faculty_in_department` exists to serve). Global
  = data corruption. Scoping to COUNT(faculty edges)>1 confines it to exactly the 14.
- **HARD GUARD:** for each scoped person, require exactly ONE keep (a faculty edge whose org IS in
  KB-home). If the scope yields 0 keeps or >1 keeps → **abort/skip that person, log it** (never demote
  both or neither). None hit this today; the guard protects future data.
- Resolves all 14 cleanly (each has exactly one KB-home keep + one demote). Esperdy included
  (KB-home = History; supersedes the earlier "ambiguous" profile-line read).
- Guiling proof: 95/95 KB items under CS, 0 under MTSM; CS edge created 06-15, MTSM 06-18 (later
  pickup). → keep `faculty@CS`, demote `faculty@MTSM`→`affiliated`.

Full 14 (keep home | → affiliated): Wang(CS|MTSM), Akansu(ECE|MTSM), Eljabiri(CS|Informatics),
Vaish(CS|Informatics), Bonchonsky(MIE|Chemistry), Rahman(MIE|SAET), Sengupta(MIE|SAET),
Feng(SAET|NJSOA), Taher(CEE|NJSOA), Cohen(HSS|NJSOA), Esperdy(History|NJSOA),
Truesdell(Informatics|Art+Design), Zarzycki(NJSOA|Art+Design), Narahara(NJSOA|Art+Design).

## Part A — data correction
Gated one-off `scripts/_fix_duplicate_faculty_home.py` (dry-run default, `--commit`,
`hardened_backup`, dev-copy first). Computes the SCOPED rule at runtime (no hardcoded ids): scope to
COUNT(faculty edges)>1, apply the KB-home keep/demote with the hard 1-keep guard, prints the 14-row
diff (person | keep | →affiliated), applies `UPDATE edges SET category='affiliated'` on the stray
edges only. Idempotent (re-run = 0 rows, category already `affiliated`).

## Part B — schema migration: GENERALIZE the EXISTING migration (senior-eng HIGH-2)
**Do NOT hand-write a fresh rebuild.** `scripts/_edges_category_migrate.py` ALREADY rebuilds the
STRICT `edges` table to widen this exact CHECK (it added `officer`/`deprep`): idempotent, dry-run
default, backed up, and critically it **recreates all 3 indexes incl. the UNIQUE
`idx_edges_triple(src_id,type,dst_id)`** (the edge-identity guarantee `upsert_edge` depends on), runs
`PRAGMA foreign_key_check` + rollback, and self-heals an aborted run. A from-scratch rebuild that
"preserves row count + max(id)" (my original Part B) would SILENTLY DROP those indexes → duplicate
edges become insertable + full-scan regressions on every `_primary_role`/`faculty_in_department`/
`entity_card`. So: **add `'affiliated'` to that script's `EDGES_NEW` CHECK + flip its `needs_migration`
test to `"'affiliated'" not in row[0]`**, and update the source-of-truth `schema.py::EDGES` CHECK
(line 334-335) for fresh DBs. Inherits index recreation + FK check + self-heal for free.
- Confirmed by review: 0 tables FK-reference `edges.id`; no triggers on `edges`. Operational note:
  DROP+RENAME is real DDL — run on the dev copy first, and on live at low traffic / briefly stop bots
  to avoid `SQLITE_LOCKED`. `create_all()` (IF NOT EXISTS) won't conflict post-rebuild; the live widen
  is script-only (no SCHEMA_VERSION runner — matches how officer/deprep shipped).

## Part C — surfacing (code; needs restart) — marker MUST survive compose (both reviewers, MEDIUM-3)
Both reviewers flagged: `entity_card` + `title_of_person` are LLM-**composed**, and a parenthetical
marker like `(affiliated)` can be silently reworded/dropped by the temp-0 8b model — dropping it
reads MORE authoritative ("Professor — MTSM"), re-introducing the exact over-claim. LLM-agnostic hard
line → need a mechanical guarantee, not prompt goodwill. So:
1. `entity.py::_ROLE_RANK`: add `"affiliated": 7` (weakest — below emeritus:6) so `_primary_role`
   never picks the affiliation over home. (Verified inert to all category filters; no test breaks.)
2. **`title_of_person` → DETERMINISTIC**: add it to `_DETERMINISTIC_SKILLS` (structured_answer.py:144).
   No greeting on this surface (plain title string) → serve `format_answer` VERBATIM → marker
   guaranteed. Update `test_ws3_render.py:68` (it currently asserts title composes — owner-approved
   change; the greeting decision was for warmth, but title carries a load-bearing distinction now).
3. **`entity_card` STAYS composed** (keep the "Hi there!" opener) but PROTECT the marker:
   (a) extend `_compose_preserves_facts` (message_handler.py:187) to reject a compose that dropped an
   `(affiliated)`/`(joint appointment)` marker present in the Facts — bypassing the roster lead-in
   gate at :192 (cards never match it) → caller keeps verbatim Facts. **COUNT-AWARE (Fable):** require
   `composed.count(marker) >= facts.count(marker)` per marker (not mere presence), so a person with two
   affiliated/joint edges can't false-pass when the model keeps one marker and drops the other. This is
   the real, model-agnostic guarantee. (b) one reinforcing line in `compose_from_rows` prompt
   (ollama_client.py:420): preserve parenthetical role qualifiers verbatim.
4. Annotate the org label by category via ONE shared helper (entity_card + title_of_person never
   drift): `joint`→`"{org} (joint appointment)"`, `affiliated`→`"{org} (affiliated)"`. **Suppress the
   marker when the title is the bare category fallback** (`[cat]`) so a title-less edge never renders
   `"affiliated — MTSM (affiliated)"`. Dedup keys on the annotated pair.
   - Result: Guiling card → "…— Computer Science", "…— Data Science (joint appointment)", "…— MTSM
     (affiliated)". title_of_person → "…, Computer Science; …, Data Science (joint appointment); …,
     MTSM (affiliated)."
5. `faculty_in_department`: **no change** (already `category='faculty'` → retagged edges drop out).
6. `dashboard/app.js` `ROLE_LABELS` (~:488): add `affiliated`/`joint`/`emeritus` (today they fall
   through to "Officer" in the People editor — pre-existing; fix now). Non-blocking.
7. OUT OF SCOPE (flag): `people_by_role`/`role_in_org`/`people_in_org` still traverse the demoted
   edge (it keeps its professor title) → a role/people query scoped to MTSM could surface Wang
   UNMARKED. Minor surface; the shared helper covers only the two named surfaces, not the whole
   system. Noted, deferred.

## Order / gating
backup → **B** (schema) → **A** (data), dev-copy + dry-run + inspect 14-row diff, then `--commit`
live. **C** = separate code commit → bot restart (data-only needs none; code does). No re-embed
(edges aren't embedded).

## TDD / tests
- `_fix_duplicate_faculty_home.py`: unit — (i) seeded 2-home person → non-KB-home faculty edge
  →`affiliated`, KB-home + joint + admin untouched; (ii) idempotent second run = 0 changes;
  (iii) **single-home person whose KB org_id ≠ edge org_id (HCAD split) = 0 changes** (proves the
  scope, not just the rule — senior-eng CRITICAL-1); (iv) **0-KB graph-only faculty member (Theater)
  = 0 changes**; (v) a scoped person yielding 0-keep or >1-keep → skipped + logged, not demoted.
- schema: after migration, `category='affiliated'` INSERT succeeds; row count + max(id) preserved.
- surfacing: `entity_card`/`title_of_person` mark joint + affiliated; `faculty_in_department` for
  MTSM excludes Wang/Akansu. Update existing card/title tests that assert the old flat format.
- eval: add "who are MTSM faculty" (assert NOT Wang/Akansu, no "Hong Kong") + "who is Guiling Wang"
  (assert MTSM shown as affiliated) to `eval/questions.txt`.

## Known limitation (loud, per review-against-plan)
`upsert_edge` matches `(src_id,type,dst_id)` and UPDATEs category → a future `explore` re-crawl that
re-encounters a person on the second dept's *ordinary* faculty page flips category back to
`faculty`, reverting the fix (~12 of 14). Guiling/Akansu durability depends on their MTSM listing
section header actually matching `section_policy._ROLLUP_SECTION` (`affiliat|courtesy|…`) so `route()`
skips it — **inspect `raw_pages` for the MTSM listing to confirm; don't promise.** Extra nuance
(senior-eng MEDIUM-4): even when skipped, the change-gated **M3 edge sweep** (`deactivate_edges`) may
set the untouched affiliated edge `is_active=0` — *erasing* the affiliation rather than preserving it.
So "durable as affiliated" is doubly uncertain. **Durable fix = the deferred producer change
(`explore.py`/`project.py`), parked by owner.** This corrects live data now; NOT yet crawl-proof.

## Goals checklist
- [ ] Data: 14 stray home edges → `affiliated` (relabel, is_active=1) — **shipped**
- [ ] Schema: `edges` CHECK widened to allow `affiliated`, ids/rows preserved — **shipped**
- [ ] Surfacing: `affiliated` AND `joint` marked in entity_card + title_of_person — **shipped**
- [ ] `faculty_in_department` excludes affiliated (no code change needed) — **verified**
- [ ] Producer/crawler durability — **DEFERRED (owner), flagged loud**
- [ ] "Also affiliated: …" line on faculty_in_department — **out of scope (optional)**
