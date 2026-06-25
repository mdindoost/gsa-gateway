# Office of the Registrar crawler — Crawling 2.1 (Bursar-copy)

**Date:** 2026-06-24
**Author:** Claude (delegated office rollout, owner: Mohammad)
**Status:** BUILD — TDD green; pending hard-gate reviews (senior-eng + RAG) before live.
**Touches:** new `v2/core/ingestion/registrar_crawl.py`, `scripts/crawl_registrar.py`,
`scripts/_registrar_cleanup_migrate.py`, `v2/tests/test_registrar_*.py`,
`v2/tests/fixtures/registrar/`, `eval/questions.txt`. Zero edits to existing files.

## Goal

Member #1 of the delegated office rollout (Registrar → Grad Admissions → OGI → Financial Aid →
Dean of Students → C-CAPS → Career Development). Bring `www.njit.edu/registrar/` into the KB/KG
as a dedicated Crawling-2.1 crawler (verbatim prose → `policy` KB + the staff directory → KG
staff, `source='crawler'`), then a SEPARATE gated clean-replace migration retiring the legacy
rows. Obeys the 2026-06-23 hard lines (verbatim/never-withheld, mechanical-clean-only,
crawl-brings-data-only, no staging). Idempotent content-hash recrawl.

## Recon (real crawler dry-run, 2026-06-24)

`registrar_crawl.extract_entry` would have been the tool; recon used the Bursar extractor as a
read-only probe. Result: **57 prose pages, 0 skipped, not truncated**. The Registrar **publishes a
named staff directory** (`/registrar/directory/mallstaff.php`) — a 3-column `Name / Phone /
Functions` table with **13 staff** (the Bursar parser scored it 0 because its format differs).

## Delta vs the Bursar template

1. **Roster (the only real code delta).** Registrar's roster is a POSITIONAL table
   (`Surname, Given` / space-phone / title), vs Bursar's email-anchored personnel block. New
   `parse_roster`:
   - anchors on the `Name / Phone / Functions` column header;
   - reads `(name, phone, title…)` triplets; normalizes `Trombella, Jerry → Jerry Trombella`
     and `973 596 3236 → 973-596-3236`;
   - **anti-fab boundary**: a line only becomes a Person when it is `Surname, Given` shaped —
     EVERY pre-comma token capitalized (accepts multi-token surnames `Van Pelt`/`De La Cruz`
     — senior-eng **S1** — yet rejects a comma-bearing TITLE whose clause has lowercase words,
     e.g. `Asst. Registrar for Graduation, Veterans …`). A non-name / phone-less / title-less /
     **duplicate-homonym** row **warns**, never fabricates, never silent-drops (senior-eng **S2**).
     A title that merely contains a phone no longer truncates the record (senior-eng **N1**).
2. **Per-person emails captured (RAG review).** The staff table's visible text shows no email,
   but each name is a `mailto:` anchor carrying a personal njit.edu address. `_emails_from_html`
   reads them from the raw HTML (clean_text strips hrefs) and attaches one per person by name —
   honoring complete-coverage / never-withhold. A departmental **function mailbox** is guarded
   out (never attached to a named Person). All 13 staff get an email; surfaced on the
   `entity_card` route ("what is X's email"). Staff dedup is by NAME (no unique email key on the
   text layer).
3. **`ingest_registrar`** under the existing `registrar` org (id 24); merges only `phone`
   (email empty). Otherwise identical to `ingest_bursar` (policy KB, content-hash idempotency).
4. **Cleanup (G7).** Same shape as Bursar; URL anchor `njit\.edu/registrar(/|$)`. People-retire
   matches by NAME. Registrar has **0 pre-existing KG people**, so people-retire is normally [].
   Retire set = **463 njit-crawl + 1 dashboard `contact` stub** (URL `/registrar/`) = 464 rows;
   KEEP every crawler row + any off-site/NULL-URL dashboard row.

## Notes / flags

- **Big enumerative pages included verbatim** per the never-withhold + complete-coverage hard
  lines: Dean's List (~108KB), Spring 2026 Final Exam Schedule (~90KB), graduation participant
  lists (~80/33/12KB). These are public NJIT pages → served verbatim, NOT dropped. ⚠️ They will
  be truncated at embed time by the known **M2** 2000-char limit (deferred, separate project) —
  flagged, not silently degraded.
- **ND6 departure reconciliation** deferred-with-flag (recrawl is change-detection only), same as
  EOS/IST/GSO/Bursar.
- **ND1 convergence:** still a Bursar-copy; fold into one config-driven engine later (the
  Registrar roster being email-free is one more reason to unify the two roster shapes then).

## Test plan (TDD) — 16 tests, all green

- `roster`: 13 named staff from the real fixture, name/phone normalization, cue-less title
  captured positionally, comma-title not split, non-roster page → empty, office-label → warn.
- `ingest`: staff + policy prose idempotent; phone attr, no email; org/person reused.
- `classify` / `scope` / `prose` / `cli` (dry-run writes nothing) / `cleanup` (retire set +
  pre-crawl safety guard). Bursar suite stays green (13); full collection 1149, no errors.

## Hard-gate reviews (2026-06-24) — folded

| # | Finding | Resolution |
|---|---|---|
| RAG-1 | Staff page publishes per-person emails in `mailto:` hrefs; parser dropped them | **Captured** via `_emails_from_html` + function-mailbox guard (§Delta 2); regression tests added. |
| RAG-2 | "who is the university registrar" doesn't route to the KG (`registrar` ∉ `_ROLE_VOCAB`; "University Registrar" head ordering) | **Deferred — separate router gate** (like IST's "who runs IST"). RAG-over-prose answers it (router falls through cleanly, verified). Listed below. |
| RAG/Eng-M2 | Big enumerative pages (Dean's List ~108KB…) truncate at embed | Keep verbatim (never-withhold); M2 dependency made explicit + deferred. |
| Eng-S1 | `_NAME` rejected multi-token surnames → future hire silently lost | Regex broadened (every pre-comma token capitalized); space-surname test added. |
| Eng-S2 | Homonym/duplicate name silently dropped | **Warns** in `parse_roster` + `extract_entry`; homonym test added. |
| Eng-N1 | Title containing a phone truncated the record | Terminator hardened to phone-ONLY lines; test added. |
| Eng-N2 | Cleanup global node-deactivate could orphan a 2nd appt | Inherited from reviewed Bursar; inert (Registrar has 0 pre-existing people). Accepted. |
| Eng-N3 | Recon counts unverified in spec | **Verified** by dev-copy crawl: 57 prose, 13 staff, retire set 464. |

## Goals checklist (shipped/deferred — per review-against-plan rule)

- [x] G1 `registrar_crawl.py` (path-scoped DFS + verbatim prose + table roster) + 13 staff + emails
- [x] G2 `crawl_registrar.py` gated driver (dry-run default, hardened backup, `--embed`)
- [x] G3 `_registrar_cleanup_migrate.py` G7 clean-replace (464 rows; name-based people; guarded)
- [x] G4 TDD suite (21) green + zero-regression (new files only) + eval questions added
- [x] G5 hard-gate reviews (senior-eng + RAG) folded (table above)
- [ ] G6 dev-copy verified ✓; live + embed + chat-verify + merge — IN PROGRESS
- [ ] DEFERRED: **role-lookup gap** ("registrar" → `_ROLE_VOCAB` + title-head ordering, own router gate);
      **homonym person-key disambiguation** (`_slug(name)` collision — warns today, disambiguation TBD);
      ND6 departure reconcile; ND1 engine convergence; M2 embed-truncation on big pages
