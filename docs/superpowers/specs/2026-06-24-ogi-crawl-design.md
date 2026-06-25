# Office of Global Initiatives (OGI) crawler (Crawling 2.1, office rollout #3) — design-delta

**Date:** 2026-06-24  **Status:** in progress  **Office:** #3 of the delegated rollout
**Template:** copy of `admissions_crawl.py`. Per the ND1 decision (no engine; cut per-office
ceremony) this is a design-delta note, not a full spec.

## Scope
Full crawl of `www.njit.edu/global/` (site = source of truth) into the existing `ogi` org (id 16,
Office of Global Initiatives, under njit). OGI content is immigration-heavy (F-1/J-1/OPT/STEM/H-1B/
visas/taxes/travel); served verbatim — the serve-time high-stakes heads-up (`bot/core/headsup.py`)
covers staleness/confirm-with-office. Recon: 109 pages; 1 pre-existing person (Rebecca Wolk,
`njit-crawl/ogi/...`); ~63 legacy KB rows (43 dashboard + 20 njit-crawl + a couple migration) at
njit.edu/global. Staff roster on `/office-global-initiatives-staff` (8 people).

## Delta vs Admissions (the only new code)
1. **Roster parser is "VIEW PROFILE"-ANCHORED.** On the staff page each person is the detail block
   right after a `View Profile` marker: `Name / full-title(s) / email / phone / "Official" / location`.
   Summary cards (`Name / short-title / View Profile`) and section headers (`Executive Director`) are
   skipped — only the post-`View Profile` block is parsed. Index-based; captures phone (line after
   the email). Anti-fab: name must be a person-name shape with no role keyword; a title must exist; a
   function mailbox (`global@`) is never a person; a failed block WARNS. Recount sanity warning when
   parsed people ≠ personal-email count.
2. **Names may carry a middle initial** ("James A Jones", "Vaughn C. Rogers") — `_NAME` allows it.
3. **People URL-gated to `/office-global-initiatives-staff`** (exact path suffix) — every other page
   is prose-only.
4. **Org constants:** slug `ogi`, name `Office of Global Initiatives`, parent `njit`.

## G7 + alias
- `_ogi_cleanup_migrate.py`: retire njit-crawl/migration KB + dashboard KB pointing at njit.edu/global;
  supersede pre-crawler PEOPLE (key NOT `crawler/...`, i.e. the njit-crawl Rebecca Wolk + any dashboard)
  by EMAIL match; name-only/no-email ⇒ KEPT FOR OWNER REVIEW (never auto-dropped).
- `_ogi_alias_migrate.py`: add `global initiatives` / `office of global initiatives` to org 16 (slug
  `ogi` already resolves the acronym).

## Files
`v2/core/ingestion/ogi_crawl.py`, `scripts/crawl_ogi.py`, `scripts/_ogi_cleanup_migrate.py`,
`scripts/_ogi_alias_migrate.py`, `v2/tests/test_ogi_crawl.py`, fixture `v2/tests/fixtures/ogi_staff.html`.

## Guardrails (all kept)
hardened_backup + dry-run + dev-copy-first; verbatim/mechanical-clean; anti-fab (function-email +
honest-partial); evidence-before-claims; TDD. The novel View-Profile parser gets a focused review.

## Flow
design-delta → TDD → dev-copy crawl+inspect → focused review of the parser → live crawl+embed →
chat-verify → G7 → alias → merge → owner digest.
