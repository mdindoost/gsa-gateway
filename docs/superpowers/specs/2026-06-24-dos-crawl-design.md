# Dean of Students (DOS) crawler (Crawling 2.1, office rollout #5) — design-delta

**Date:** 2026-06-24  **Status:** in progress  **Office:** #5 of the delegated rollout
**Template:** copy of `ogi_crawl.py`. Design-delta note per the ND1 decision (no engine; cut ceremony).

## Scope & recon
Full crawl of `www.njit.edu/dos/` into the existing `dean-of-students` org (id 20, under njit).
Recon: 26 pages (academic integrity, conduct, hazing, bias response, excusals, medical withdrawal,
AI-use policy, etc.). Roster on `/dos/contact.php` — **7 people**. Legacy: 26 njit-crawl + 1 dashboard
+ 1 migration KB rows; **7 pre-existing dashboard people** (Boger, Dowd, Damell, Bullock, Williams,
Rodgers, Edwards — all email-less, matching the page).

## Delta vs OGI (the only new code) — a THIRD roster shape
On the contact page each person is a block that ENDS with a `View Profile` marker:
    <role/section header>  /  Surname, Given  /  <title line(s)>  /  View Profile
No per-person email (only the departmental `dos@` mailbox); names render `Surname, Given`.
`parse_roster`:
  * splits the roster on `View Profile` (the per-person END delimiter);
  * the name is the `Surname, Given` line in the block that carries NO role keyword (reordered to
    `Given Surname` for KG consistency); the line(s) after it = the title; the line before = the
    section header, **persisted** to a later headerless block (the 7th person, Edwards, reuses the
    prior `Administrative Staff` header);
  * a title with a comma (`Executive Assistant, Dean of Students…`) is NOT mistaken for a name (it
    carries role keywords); a no-name block is skipped (preamble/non-person); a name-without-title
    WARNS; a recount warning fires if people parsed ≠ `View Profile` count.
People URL-gated to `/dos/contact.php`. `StaffRecord` carries name + title(s) only (no email/phone).

## G7 + alias
- `_dos_cleanup_migrate.py`: retire njit-crawl/migration KB + dashboard KB on njit.edu/dos; supersede
  pre-crawler people by **NORMALIZED NAME** (no email to match on — the registrar precedent; tiny
  office, crawler reproduces the exact roster). Non-matching ⇒ KEPT FOR OWNER REVIEW.
- `_dos_alias_migrate.py`: add `dean of students` / `dos` / `office of the dean of students` to org 20.

## Files
`v2/core/ingestion/dos_crawl.py`, `scripts/crawl_dos.py`, `scripts/_dos_cleanup_migrate.py`,
`scripts/_dos_alias_migrate.py`, `v2/tests/test_dos_crawl.py`, fixture `v2/tests/fixtures/dos_contact.html`.

## Guardrails (all kept)
hardened_backup + dry-run + dev-copy-first; verbatim/mechanical-clean; anti-fab (name/title
discrimination + honest-partial); evidence-before-claims; TDD. Novel parser → focused review.

## Flow
design-delta → TDD → dev-copy crawl+inspect → focused review of the parser → live crawl+embed →
chat-verify → G7 → alias → merge → owner digest.
