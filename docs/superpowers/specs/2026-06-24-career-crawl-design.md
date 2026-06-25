# Career Development Services crawler (Crawling 2.1, office rollout #7, LAST) — design-delta

**Status:** in progress  **Office:** #7 (final) of the delegated rollout  **Template:** copy of
`dos_crawl.py` (SAME roster shape). Design-delta note per the ND1 decision.

## Scope & recon
Full crawl of `www.njit.edu/careerservices/` into the existing `career-development` org (id 18,
Career Development Services, under njit). Recon: roster on `/careerservices/contact-us` — **16 unique
people** (View-Profile-terminated `<header>/Surname,Given/title` blocks, NO per-person email — the DOS
shape exactly). Legacy: 29 njit-crawl + 1 dashboard KB; **20 pre-existing people** (16 dashboard +
4 njit-crawl). Several staff are CROSS-LISTED under two teams (Lanzot, Sims) → deduped by name.

## Delta vs DOS — NONE in parsing (reuses the reviewed DOS parser)
`parse_roster`, the `Surname, Given` detector, section-header persistence, and the exact-path people
gate are reused UNCHANGED → this office adds NO novel parsing, so no separate focused review (review
fires on novelty only). The ONLY change: the recount sanity-check now accounts for cross-listed people
(a person under N teams = N 'View Profile' markers), so it isn't a false alarm — tested. Constants:
slug `career-development`, name `Career Development Services`, contact path `/careerservices/contact-us`.

## G7 + alias
- `_career_cleanup_migrate.py`: retire njit-crawl/migration KB + dashboard KB on njit.edu/careerservices;
  supersede pre-crawler people by NORMALIZED NAME (no email on the roster — DOS precedent). The 16
  dashboard people + 2 njit-crawl (Pyar, Kennedy) match; 2 njit-crawl spelling-variant dups
  (**"Anthony Yurista"** vs crawler "AJ Yurista"; **"Carolina Barbagranda"** vs "Carolina Barba Granda")
  have no name match ⇒ KEPT FOR OWNER REVIEW (recommend retire — same-person stale variants).
- `_career_alias_migrate.py`: add career services / career development / career development services /
  cds to org 18.

## Files
`v2/core/ingestion/career_crawl.py`, `scripts/crawl_career.py`, `scripts/_career_cleanup_migrate.py`,
`scripts/_career_alias_migrate.py`, `v2/tests/test_career_crawl.py`, fixture `v2/tests/fixtures/career_contact.html`.

## Guardrails (all kept)
hardened_backup + dry-run + dev-copy-first; verbatim/mechanical-clean; anti-fab (honest-partial —
the 2 variant dups go to review, never auto-dropped); evidence-before-claims; TDD.

## Flow
design-delta → TDD → dev-copy crawl+inspect → live crawl+embed → chat-verify → G7 → alias → merge →
owner digest (incl. the 2 spelling-variant dups for review). **This completes the 7-office rollout.**
