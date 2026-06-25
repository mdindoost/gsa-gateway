# Office of University Admissions crawler (Crawling 2.1, office rollout #2) — design-delta

**Date:** 2026-06-24  **Status:** in progress  **Office:** #2 of the delegated rollout
**Template:** copy of `registrar_crawl.py`. Per the ND1 decision (no engine; cut per-office
ceremony) this is a *design-delta* note, not a full spec — it records only what differs from the
registrar crawler and the owner decisions that scope it.

## Owner decisions (escalated 2026-06-24, owner chose)
- **Option C — full crawl; the live site is the source of truth; cover & believe everything on it.**
- **Org scope = the OFFICE OF UNIVERSITY ADMISSIONS** (org 21, already named that), NOT graduate-only.
  Crawl the whole `www.njit.edu/admissions/` subtree + the full ~26-person team. Keep the slug
  `graduate-admissions` (avoid breaking refs); add aliases `university-admissions` + `admissions`.
- **Grad-advisors page = PROSE ONLY, mint 0 people.** `/admissions/graduate/graduateadvisors.php`
  lists 71 people who are existing KG **faculty** cross-listed as program advisors (verified:
  Dhar→Math, Sahin→BME, Soares→Biology, Rojas-Cessa→ECE, Datta→MIE, Mitra→Chem). Minting them would
  duplicate faculty and violate home-appointment-only. Ingest the page verbatim as KB; extract no people.

## Delta vs Registrar (the only new code)
1. **Roster parser is EMAIL-ANCHORED + section-grouped** (registrar's is a `Name/Phone/Functions`
   table — does not match). On the contact page each person renders as:
       [section header]            # University Admissions | Recruitment - Undergraduate | … | Operations
       Given Surname               # may carry a parenthetical nickname: "Yenitza (Jenny) Ruiz"
       <title line(s)>             # 1–2 lines (long titles wrap); always >=1
       localpart@njit.edu          # personal email closes the person
   Parse: forward scan; a personal email EMITS the buffered (name, titles); a section header sets the
   current unit and resets the buffer (this discards the office address/hours preamble and the
   duplicate "Surname, Given" leadership summary cleanly); a function mailbox (admissions@…) resets
   without emitting. Name = first buffered line; titles = the rest. Title-vs-name guard: the name line
   must NOT contain a role keyword (Provost/Director/Recruiter/Manager/Coordinator/Assistant/…); a
   block whose head looks like a title, or that has no title, WARNS — never fabricates/silent-drops.
2. **People are extracted ONLY from the contact-admissions page (URL-gated).** Every other page —
   including graduateadvisors.php and admitted-students (56 *function* mailboxes for other offices) —
   is prose-only. This is the mechanism that enforces "grad-advisors = 0 people".
3. **Emails come from the visible text** (the contact page prints them as text, not just mailto
   hrefs), so the registrar `_emails_from_html` mailto reader is not needed; emails are captured
   inline by the parser. Function-mailbox anti-fab guard retained.
4. **Org constants:** slug `graduate-admissions`, name `Office of University Admissions`, parent `njit`.

## Recon facts (read-only)
- 34 prose pages under /admissions/ (undergrad/grad/intl/transfer/finaid/deadlines/FAQs/tuition).
- Office team on /admissions/contact-admissions: ~26 people, grouped by section, each w/ title+email.
  Matches the 26 pre-existing dashboard people (id 333–358, batch-authored 2026-06-17) — crawl confirms.
- G7 legacy to clean-replace: 13 dashboard rows at njit.edu/admissions/* (+1 contact stub id 4392).
  **Exclude management.njit.edu** (26 MTSM Ph.D. admission-req rows = a DIFFERENT office, out of scope) —
  the G7 URL regex anchors to `njit.edu/admissions` only.

## Files
- `v2/core/ingestion/admissions_crawl.py` — crawler (copy + the email-anchored parser).
- `scripts/crawl_admissions.py` — gated runner (seed https://www.njit.edu/admissions/).
- `scripts/_admissions_cleanup_migrate.py` — G7 clean-replace; supersede the 26 dashboard people by
  parenthetical-normalized NAME (roster nicknames vs manual plain names) or email; **leftover
  non-matching dashboard people are LISTED for the owner, never auto-dropped** (honest-partial).
- `scripts/_admissions_alias_migrate.py` — add aliases university-admissions/admissions to org 21.
- `v2/tests/test_admissions_crawl.py` — fixture-driven (the saved contact + advisors pages).

## Guardrails (unchanged, all kept)
hardened_backup + dry-run + dev-copy-first; verbatim/mechanical-clean; anti-fab (function-email +
honest-partial); evidence-before-claims; TDD. The novel email-anchored parser gets a focused expert review.

## Flow
design-delta (this) → TDD → dev-copy crawl+inspect → live crawl+embed → chat-verify → G7 clean-replace
→ alias → focused expert review of the parser → merge → owner digest.
