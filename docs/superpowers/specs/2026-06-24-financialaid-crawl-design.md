# Office of Financial Aid crawler (Crawling 2.1, office rollout #4) — design-delta

**Date:** 2026-06-24  **Status:** in progress  **Office:** #4 of the delegated rollout
**Template:** copy of the **Bursar prose-only** crawler (`bursar_crawl.py`). Per the ND1 decision
(no engine; cut per-office ceremony) this is a design-delta note. Reuses the already-reviewed Bursar
roster parser UNCHANGED (only constants + the function-mailbox list differ) → **NOT novel → no
focused review** (review fires on novelty only).

## Scope & recon
Full crawl of `www.njit.edu/financialaid/` (site = source of truth) into the existing `financialaid`
org (id 28, Office of Financial Aid, under njit). Recon: **124 pages**, prose-heavy (loans, FAFSA,
SAP, scholarships, work-study, COA, deadlines, verification, appeals). **No named-staff roster page** —
contact-us lists only `finaid@njit.edu` + phone. A few individual contacts appear INLINE in prose
(e.g. veterans certifying official Ms. Allison Babinski; scholarship contacts pat.zappone@, Kkline@) —
these are captured VERBATIM in KB, NOT minted as KG staff (there is no roster to parse). Funding is
high-stakes → the serve-time heads-up (`bot/core/headsup.py`) covers it.

This is the **Bursar pattern**: prose-only office, **0 KG Person nodes** is the correct, honest
outcome. The `personnel`-anchored roster parser returns [] (no such anchor on FA pages); the
function-mailbox guard (finaid@/eop@/honors@/admissions@/registrar@/…) keeps that robust.

Legacy: **61 njit-crawl KB rows** + **1 pre-existing njit-crawl person (Ivon Nunez)**. No dashboard rows.

## G7 + alias
- `_financialaid_cleanup_migrate.py`: retire njit-crawl/migration KB + any dashboard KB on
  njit.edu/financialaid; supersede pre-crawler people (key NOT `crawler/`) by EMAIL. With 0 crawler
  people, the 1 njit-crawl person **Ivon Nunez has no email match ⇒ KEPT FOR OWNER REVIEW** (never
  auto-dropped) — flagged in the digest for the owner to retire-or-keep.
- `_financialaid_alias_migrate.py`: add `financial aid` / `financial aid office` / `student financial
  aid services` / `sfas` to org 28.

## Files
`v2/core/ingestion/financialaid_crawl.py`, `scripts/crawl_financialaid.py`,
`scripts/_financialaid_cleanup_migrate.py`, `scripts/_financialaid_alias_migrate.py`,
`v2/tests/test_financialaid_crawl.py`, fixture `v2/tests/fixtures/financialaid_contact.html`.

## Guardrails (all kept)
hardened_backup + dry-run + dev-copy-first; verbatim/mechanical-clean; anti-fab (function-email +
honest-partial — Ivon Nunez kept for review); evidence-before-claims; TDD.

## Flow
design-delta → TDD → dev-copy crawl+inspect → live crawl+embed → chat-verify → G7 → alias → merge →
owner digest (incl. the Ivon-Nunez keep-for-review decision).
