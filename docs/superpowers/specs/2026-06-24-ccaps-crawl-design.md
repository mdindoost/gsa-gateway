# Counseling Center (C-CAPS) crawler (Crawling 2.1, office rollout #6) — design-delta

**Status:** in progress  **Office:** #6 of the delegated rollout  **Template:** copy of `ogi_crawl.py`.
Design-delta note per the ND1 decision (no engine; cut ceremony).

## Scope & recon
Full crawl of `www.njit.edu/counseling/` into the existing `counseling` org (id 19, Counseling Center
(C-CAPS), under njit). Recon: 15 pages (services, crisis/get-help, group therapy, sexual-assault
response, teletherapy partners, scheduling). Roster on `/counseling/c-caps-staff` — **7 people**.
Counseling is sensitive (mental health) → served verbatim; serve-time heads-up covers it.
Legacy: 26 njit-crawl + 1 dashboard + 2 migration KB; **8 pre-existing njit-crawl people** — note one
is a TYPO DUPLICATE ("Kalpan Daswani" / kalpan.daswani vs the real "Kalpana Daswani" / kalpana.daswani).

## Delta vs OGI (the only new code) — a 4th roster shape (EMAIL-anchored + credential)
Each person renders:
    Given Surname, <Credential(s)>     (e.g. 'Phyllis Bolling, Ph.D.', 'Maham Tariq, MA, LPC')
    <title line(s)>                    (e.g. Director / Licensed Psychologist)
    Phone: <number>
    Email:
    localpart@njit.edu
`parse_roster` is email-anchored: the email closes a person; the NAME is the first 'Given Surname'
line in the block with no role keyword (the credential after the first comma is STRIPPED from the
stored name — kept verbatim in prose); the non-meta lines between the name and the Phone:/Email:
labels are the title(s); the phone is read from the per-person 'Phone:' line (NOT the office number in
the preamble). Anti-fab: function mailbox never a person; no-name / no-title WARNS; recount warning.
People URL-gated to the exact path `/counseling/c-caps-staff`. Names allow diacritics/middle initials.

## G7 + alias
- `_ccaps_cleanup_migrate.py`: retire njit-crawl/migration KB + dashboard KB on njit.edu/counseling;
  supersede pre-crawler people (key NOT `crawler/`) by EMAIL. The 7 real people match by email; the
  typo dup "Kalpan Daswani" (kalpan.daswani) has NO crawler email match ⇒ KEPT FOR OWNER REVIEW
  (flag as a likely-retire stale typo).
- `_ccaps_alias_migrate.py`: add c-caps / ccaps / counseling center / counseling and psychological
  services / counseling to org 19.

## Files
`v2/core/ingestion/ccaps_crawl.py`, `scripts/crawl_ccaps.py`, `scripts/_ccaps_cleanup_migrate.py`,
`scripts/_ccaps_alias_migrate.py`, `v2/tests/test_ccaps_crawl.py`, fixture `v2/tests/fixtures/ccaps_staff.html`.

## Guardrails (all kept)
hardened_backup + dry-run + dev-copy-first; verbatim/mechanical-clean; anti-fab (function-email +
honest-partial); evidence-before-claims; TDD. Novel parser → focused review.

## Flow
design-delta → TDD → dev-copy crawl+inspect → focused review of the parser → live crawl+embed →
chat-verify → G7 → alias → merge → owner digest (incl. the Kalpan-Daswani typo-dup review note).
