# Retrieval Facet — Code-Review Follow-ups (2026-06-14)

Non-blocking findings from the high-recall review of the P2.5 research-area facet branch
(`origin/main..HEAD` at push `1617cb5`). All cleanup/perf/consistency — no correctness
bug in deployed behavior. Tracked here because `gh` is unavailable in the environment.

Status: ☐ open · ☑ done

---

## Retrieval-side cleanup

- [x] **1. N+1 in `_display_name`** (`v2/core/retrieval/skills.py`)
  `people_by_area_tag`, `faculty_in_department`, and `people_by_research_area` each call
  `_display_name(conn, e)` per entity, and `_display_name` itself runs up to 2 point
  queries. For N results that's up to 2N queries after the main scan.
  **Fix:** add `_display_names(conn, entity_ids) -> dict[str,str]` doing one
  `entity_id IN (?,…)` lookup (prefer `profile`, fall back to `overview`); refactor the
  three skills to build the dict once and look up in memory.

- [x] **2. `areas_in_org` duplicates `area_counts` grouping** (`v2/core/retrieval/skills.py`)
  Both casefold-group and canonicalize independently. `areas_in_org` is just the area
  names of `area_counts`.
  **Fix:** `return sorted((a for a, _ in area_counts(conn, org_id)), key=str.casefold)`.
  One grouping mechanism, so the two facets can't drift on the area set or display casing.

- [x] **3. `_canonical` case-sensitive tie-break** (`v2/core/retrieval/skills.py`)
  Grouping is casefold but the alphabetical tie-break (`kv[0]`) is case-sensitive, so a
  tie between `machine learning` and `Machine Learning` is broken by ASCII (`M`<`m`)
  rather than naturally.
  **Fix:** tie-break on `kv[0].casefold()` (then raw as final stabilizer).

## Router phrasing gaps

- [ ] **4. `_RANK` misses bare "how many [research areas]"** (`v2/core/retrieval/router.py`)
  "how many research areas does CS have" → no `_AREA_TRIGGER` verb (area=None), `_RANK`
  only matches "how many **people**", so it routes to `areas_in_org` (full list) instead
  of a count.
  **Fix:** either add a count-of-areas shape, or treat bare "how many … areas" as a
  count. Lower priority — there is no count-of-areas skill yet; decide scope first.

- [ ] **5. Router precedence: enum branch shadows faculty roster** (`v2/core/retrieval/router.py`)
  `_ENUM_AREAS` is checked before the `faculty`/`professor` branch, so
  "show all faculty and their research areas in CS" routes to `areas_in_org` instead of
  the faculty roster.
  **Fix:** add explicit overlap handling/precedence + tests for the compound phrasings.

- [ ] **6. `_PROSE_BOUNDARY` truncates areas after abbreviation period / standalone "I"**
  (`v2/core/ingestion/njit_adapter.py`) — e.g. "Machine Learning, Approx. Algorithms"
  loses "Algorithms"; "Type I Diabetes …" truncates at "Type".
  Documented precision/recall tradeoff from the structural-extraction commit. **Evaluate
  whether to refine** (abbreviation allowlist / require lowercase-prose context) or leave.

## Ingestion-side (fold into next ingestion pass, or now while context is hot)

- [x] **7. Unify `backfill` + `repair` on one area definition**
  (`scripts/backfill_research_area_tags.py`, `scripts/repair_paren_fragmented_areas.py`)
  Both reconstruct areas from stored content but `backfill` uses a raw delimiter split
  while `repair` routes through `njit_adapter._split_areas` (the canonical filters).
  They disagree on rows like id=3313 (backfill recovers 2 grouped areas; repair chose
  `[]`). Also duplicated argparse/backup/dry-run/executemany skeletons.
  **Fix:** route `backfill` recovery through `_split_areas` so there is ONE definition;
  extract the shared "metadata migration runner" (backup-first, dry-run default) into a
  helper both scripts call. Reuse a hardened backup (WAL sidecars + integrity check) like
  `ingest_faculty._auto_backup` rather than bare `shutil.copy2`.
