# Bursar Crawl — Design Spec

**Date:** 2026-06-24
**Author:** Mohammad Dindoost + Claude
**Status:** Design — HARD-GATE reviews DONE (both APPROVE-WITH-CHANGES; findings folded) → **pending
owner sign-off** (incl. the M2 ship-now-vs-fix-first decision) → TDD build.

## Summary

Add NJIT's **Office of the Bursar / Student Accounts** (`www.njit.edu/bursar/`) to the DB under the
**uniform department model**, as the next **Crawling 2.1** office. The bot should answer everyday
billing questions: payments/refunds/holds, tuition & fee schedule, eRefund (direct-deposit) setup,
1098-T and other forms, billing/refund deadlines (important dates), the Financial Responsibility
Agreement, the FAQs, how to schedule an appointment, and the office's contacts/hours.

This is a **copy of the Graduate Studies (GSO) crawler** (`v2/core/ingestion/gradstudies_crawl.py`,
itself a copy of EOS), adapted for `/bursar/`. The proven EOS/IST/GSO crawlers are **not modified**
→ zero regression risk. Convergence into one config-driven engine is deferred (ND1).

Bursar becomes the **single repeatable source** for `/bursar/`, replacing today's 16 hodgepodge rows
(1 `dashboard` stub + 15 `njit-crawl`, heavily duplicated — see Existing-state cleanup, G7).

## The coverage rule this design obeys (Mohammad, 2026-06-24)

Whatever is on the site **within the entry point** (`/bursar` path-prefix on `www.njit.edu`) we grab —
verbatim, mechanically cleaned, no content exclusions, ever. The only boundary is the entry point
itself. PDFs are a **capability gap, not a content judgment** (ND5): a PDF *page* is stored verbatim
with its PDF link; PDF *bytes* are flagged, not extracted. CLAUDE.md hard lines: "NJIT web content is
served VERBATIM, never withheld" + "crawl brings data only — mechanical clean, no usage decisions."

## Recon (2026-06-24, read-only — no DB writes)

Recon was done two ways: an initial WebFetch of the homepage (which **undercounted** the site), then a
**live read-only dry-run of the actual GSO crawler pointed at `/bursar/`** (`extract_entry`,
budget 400, no DB writes) — the authoritative source below. (The two hard-gate reviews independently ran
the same dry-run and caught the WebFetch undercount; their findings are folded here.)

- **Canonical host = `www.njit.edu/bursar/`** (HTTP 200, server-rendered HTML, real text — not a JS
  shell). H1 "Home | Office of the Bursar".
- **The live crawler finds 22 in-scope `/bursar` pages from one homepage seed, 0 staff, 0 warnings, 0
  skipped, not truncated.** The exact set (path | chars | linked-files):
  ```
  /bursar/                      1093  /bursar/important-dates          8243
  /bursar/1098-t.php            5408  /bursar/node/66                  4860  f=1
  /bursar/Authorized_Users.php   380  /bursar/node/71                 23023  f=28
  /bursar/Instructions.php      1248  /bursar/node/86                  7912  f=3
  /bursar/contact-us            1546  /bursar/quick-links              1306
  /bursar/estatements           4466  /bursar/refunds                  3237  f=1
  /bursar/faq                   6509  /bursar/schedule-appointment      317
  /bursar/financial-hardship-…   205  /bursar/test1                    2750  f=2
  /bursar/for-students          1052  /bursar/touchnet-erefund         6626
  /bursar/forms                  329  /bursar/veterans-information       711
  /bursar/hardship-appeals      2863  /bursar/holds                     816  f=1
  ```
  **Recon correction (vs the WebFetch first pass):** the homepage links a `tuition-and-fee-schedule`
  and a `for-parents` that do **not** resolve to crawled in-scope pages, and the FAQ page is `faq`
  (not `faqs`). The **manifest (G4) is the authoritative page list the owner reviews** — the set above
  is the verified current reality, not a hand-guessed list.
- **`schedule-appointment` is NOT a JS shell** — it cleans to 317 ch of real prose → classified `prose`,
  stored. The crawl found **zero** skippable pages, so ND4/T4's `skip:js-shell` branch must be tested
  with a **synthetic empty-HTML fixture**, not a real bursar page.
- **PDFs are substantial (ND5):** `node/71` links **28 files**, `/forms` 6, `estatements` 4, `faq` 4,
  plus singles. Each PDF *page* is stored verbatim with its link; bytes flagged, not extracted. The
  manifest surfaces the real PDF count so the owner's ND5 review is informed.
- **KEY DIFFERENCE FROM GSO — no named personnel.** `contact-us` lists **office-level** contacts only:
  departmental emails (`bursar@njit.edu`, `healthinsurance@njit.edu`, `thirdparty@njit.edu`,
  `payment@njit.edu`, `collections@njit.edu`), phone **973-596-2877**, address (323 Dr. MLK Blvd,
  Student Mall), and office hours. **There is no "Personnel"/named-staff block** — verified: **none of
  the 22 cleaned pages contains the token `"personnel"`**, so the inherited roster parser returns `[]`
  → Bursar is a **prose-only office: 0 KG `Person` nodes** (the correct, honest outcome — not a coverage
  gap). The departmental emails are office **function** addresses (not people) and live verbatim in the
  `contact-us` prose KB. **Anti-fab hardening (RAG-review B1):** "0 people" must not rest on the
  unproven assumption that `"personnel"` never appears — see G2 for the function-email guard + negative
  test that make the guarantee robust to a future stray `"personnel"` token in nav/sidebar/boilerplate.
- **Existing DB state (verified):** `organizations` row **id 17** (`slug='bursar'`,
  name `'Office of the Bursar / Student Accounts'`, `type='office'`, parent `njit` id 1, empty
  metadata). **0 people in the KG.** **16 active `knowledge_items` tagged to org 17:** 1 `dashboard`
  (id 4388, `type='contact'` — NOT `policy` — the homepage stub, natural_key `…/bursar/`) + 15
  `njit-crawl` (the old one-off grounded pass — duplicated: eRefund ×5, FAQs ×5, plus
  contact/forms/important-dates/for-students/tuition). The G7 retire list must be built by
  **source + natural_key, NOT by `type`** (else it would miss the `type='contact'` stub).

## Goals

- **G1.** Reuse the **existing** `bursar` Org (id 17, `type='office'`, parent `njit`) idempotently via
  `ensure_org` — no new org, no rename, no alias.
- **G2.** **No fabricated people (proven, not assumed).** Bursar publishes no named staff → the crawler
  emits **0 Person nodes**. The office's contacts (emails/phone/address/hours) are captured as
  `contact-us` **prose**, verbatim. The "0 people" guarantee is made robust by TWO mechanisms, not by
  the bare assumption that `"personnel"` never appears (RAG-review B1):
  1. **A negative regression test** (T1): feed a `personnel`-anchor + office-label-line + phone +
     *function* email arrangement (the exact Bursar shape) and assert **no Person is emitted**. Today
     no page even contains `"personnel"`, but nav/sidebar/boilerplate could introduce it after a site
     change — the test pins the parser's safety, not the site's current text.
  2. **A function-email guard in `bursar_crawl.py`'s roster parser** (defense-in-depth): a denylist of
     departmental mailbox local-parts (`bursar`, `healthinsurance`, `thirdparty`, `payment`,
     `collections`, plus generic `info`, `contact`, `admissions`, `help`) → a record whose email is a
     function address does **not** become a Person; it surfaces as a warning. Root cause this closes:
     `_TITLE_CUES` contains `"office"`, so `_is_title("Office of the Bursar")` is True and the phone-
     anchored heuristic would otherwise treat the line above as a name and emit e.g. Person
     "Student Accounts" / title "Office of the Bursar" from `bursar@njit.edu`. The guard lives in the
     **bursar copy only** (ND1: proven EOS/IST/GSO modules untouched → zero regression); upstreaming it
     into the shared engine is folded into the ND1 convergence.

  If a future Bursar page *does* publish a genuine named roster, the inherited parser still captures it
  (name/title/phone/email; the guard only blocks **function** mailboxes; unparseable → warn, never drop,
  never invent).
- **G3.** **Every** in-scope `/bursar` page is a verbatim `knowledge_item` **`type='policy'`** tagged
  to `bursar`, embedded, and RAG-answerable. No content exclusions. **KNOWN LIMITATION — embed
  truncation (M2), loudly flagged (RAG-review N1):** `embed_all.py` embeds only the first **2000 chars**
  of a page. **11 of Bursar's 22 pages exceed 2000 chars** — `node/71` (23k), `important-dates` (8.2k),
  `node/86` (7.9k), `touchnet-erefund` (6.6k), `faq` (6.5k), `1098-t.php` (5.4k), `node/66` (4.9k),
  `estatements` (4.5k), `refunds` (3.2k), `hardship-appeals` (2.9k), `test1` (2.75k). The **verbatim text
  is stored intact** (serving/hard-line satisfied), and the **FTS/keyword leg searches the full
  `search_text`** (so the answer is still reachable by keyword), but the **semantic embedding only sees
  the head** → degraded semantic recall for facts deep in long billing pages (a specific deadline, a
  late fee, eRefund step 7, a 1098-T detail). This is the deferred corpus-wide **M2** issue
  ([[project_gradstudies_router_todo]]). DECISION FOR OWNER (see cover note): ship Bursar now with M2
  flagged + rely on FTS for deep facts, OR land chunk-before-embed (M2) first. Step-4 verification will
  deliberately probe a fact known to live past char 2000.
- **G4.** A **supervised first crawl**: a dry manifest (URL + proposed type + extraction preview +
  flagged PDFs/unknowns) the owner reviews before any live write.
- **G5.** **Recrawl**: re-walk the saved URL set, content-hash diff, re-embed only changed pages.
- **G6.** Output conforms to the uniform department model (Org + prose KB; people only if published).
- **G7.** **Clean replace (separate gated migration, AFTER verify):** retire the 15 `njit-crawl` rows
  and the 1 `dashboard` stub (id 4388, `type='contact'`, reproduced verbatim by the crawler's `/bursar/`
  homepage page), **keeping** any genuinely manual `dashboard` row not present on the live site (here:
  none beyond the homepage stub). Bursar ends with one clean, repeatable `source='crawler'` source.
  Mirrors `_gradstudies_cleanup_migrate.py` (incl. its dedup + alias-proofing fixes) with **two required
  adaptations the build must not miss:**
  - **URL matcher rename + anchor (senior-review B3):** `_is_gs_site_url`'s `"njit.edu/graduatestudies"`
    → an **anchored** `njit.edu/bursar` match (i.e. `/bursar/` or path-end, NOT a bare substring) so it
    covers `/bursar/`, `/bursar/node/71`, `/bursar/1098-t.php`, … but can never over-match a
    hypothetical `…/bursar-foo` row. (No such row exists today; the anchor is forward-safety.)
  - **Retire by source + natural_key, NOT by `type` (RAG-review N4):** the stub is `type='contact'`,
    so a `type='policy'`-scoped retire query would silently miss it. Build the retire set from
    `created_by IN ('njit-crawl','dashboard')` + URL match, type-agnostic.

## Non-goals (explicit deferrals)

- **ND1.** No shared/unified crawl engine yet — Bursar is a *separate copy* of the GSO/EOS crawler.
  Converge EOS+IST+GSO+Bursar into one config-driven engine later (separately-gated refactor).
- **ND2.** No off-path / off-host crawling — scope is `/bursar` on `www.njit.edu` only. Off-host links
  (TouchNet payment portal, people.njit.edu, external apps) survive verbatim in the prose; the
  live-fallback covers them on demand. Entry-point boundary, not a content exclusion.
- **ND3.** No staging / decline / office_page tier — Bursar prose is normal KB (`type='policy'`).
- **ND4.** No JS rendering — JS-only shells clean to empty → `skip:js-shell`, flagged, not faked
  (likely `schedule-appointment`).
- **ND5.** No PDF text extraction — capability gap; the PDF page+link is stored, bytes flagged
  (`project_pdf_extraction_todo`).
- **ND6.** No departure reconciliation in this build (G5 is change-detection only) — safe for the
  FIRST crawl; do not advertise repeatable-recrawl departure-retire until built.
- **ND7.** No org aliases in this build itself (acronym/alt-name links live in page content).
  ***Recommended fast-follow (RAG-review N2, promoted from "optional"):*** "Student Accounts" is **half
  the office's official title** ("Office of the Bursar / Student Accounts") yet `resolve_org("student
  accounts") → None` today (verified) — so "who handles my student account" gets no org scope. Add
  `"student accounts"` to org 17's `metadata.aliases` as a small gated task exactly like the GSO alias
  (the precedent + script pattern exist). Higher-value than a nickname because it's the literal alt-name;
  still kept OUT of the crawl build to honor the crawl/usage separation.

## Architecture

New `v2/core/ingestion/bursar_crawl.py` (copy of `gradstudies_crawl.py`, adapted: `BURSAR_SLUG`/
`BURSAR_NAME`, seed `https://www.njit.edu/bursar/`) + gated CLI `scripts/crawl_bursar.py` (copy of
`crawl_gradstudies.py`). Reuses the `web_crawler` spine (fetch/clean/`select_links`). Components are
identical to the GSO design:

1. **Discovery** — path-prefix-scoped DFS from the homepage seed (`_in_scope` = `/bursar` subtree);
   depth + page-budget bounded, truncation-flagged.
2. **Page-type classifier** — `staff-roster` (won't fire for Bursar — no personnel), `prose`,
   `skip:js-shell`/`skip:pdf`/`skip:asset`, `unknown`; `unknown`/`skip:*` flagged in the manifest,
   never silently dropped. Single parse per page.
3. **Manifest (dry run)** — candidate URLs + types + previews + flagged PDFs/unknowns to stdout, no DB
   writes. The supervised gate.
4. **Personnel parser (inherited, dormant for Bursar)** — kept from GSO for free; returns `[]` here.
5. **Prose ingester** — mechanically clean each page to verbatim text → `knowledge_items`
   (`type='policy'`, `org_id=bursar`), idempotent on the full-path natural key, content-hash for
   recrawl diff, figures/PDF links in `metadata`. Coverage rule: `contact-us` prose is kept (it carries
   the office contacts grad students need).
6. **Recrawl** — re-walk saved URLs, content-hash diff, re-embed only changed (ND6 departure deferred).

### Data flow (identical to GSO)

```
First crawl (supervised):
  seed www.njit.edu/bursar/ → path-scoped DFS → classify → MANIFEST (dry) → owner reviews
   → gated live write: ensure_org(bursar id 17) + every page → KB(policy)   [0 people expected]
   → embed_all → verify_kg + counts (evidence) → owner confirms

Clean replace (G7, separate gated migration, AFTER verify):
  dry-run the exact retire list (15 njit-crawl + 1 dashboard homepage stub; keep manual-only)
   → hardened_backup → --commit → re-verify counts

Recrawl (unsupervised):
  re-walk saved URLs → content-hash diff → re-embed changed → report (no departure retire — ND6)
```

### Output model (uniform)

- `nodes`: reuse Org `bursar` (id 17). **0 new Person nodes** (no published roster).
- `knowledge_items`: one per `/bursar` page, `org_id=bursar`, `type='policy'`, verbatim, embedded,
  `created_by='crawler'`. Reconcile is source-scoped (crawler only touches `source='crawler'`; the 16
  existing rows are untouched by the crawl — retiring them is the separate G7 migration).

## Error handling / anti-fabrication

- Fetch error / non-200 → flag in manifest, skip (no partial fabricated content).
- JS-shell empty clean → `skip:js-shell`, flagged, never faked.
- `unknown` type → flagged, never guessed.
- No people → 0 Person nodes (the honest answer); departmental function emails are NOT turned into
  people — enforced by the function-email guard + negative test (G2), not by assumption. Verbatim
  guarantee: stored prose == mechanically-cleaned page text (test asserts no rewriting). DFS never
  leaves `/bursar` (test asserts scope).
- **Routing safety — VERIFIED, no C1-bug analog (RAG-review N3):** the natural billing questions
  ("how do I set up eRefund", "1098-T form", "contact the bursar", "billing deadlines", "financial
  responsibility agreement", "schedule a bursar appointment", "how do I pay my bill") all route to
  `None` → semantic RAG (correct — they're policy asks, not enumerate/filter/count). None of
  `bursar`/`student accounts`/`collections`/`refund` trips `_AREA_TRIGGER`/`_RESEARCH_CUE`/
  `match_metric`, so the GSO "studies" C1 mis-route does **not** transfer. `bursar` resolves to org 17
  where named. Bursar KB is `type='policy'` → in the default answer corpus (only `publication`/`webpage`
  excluded), so it is surfaced.
- **`ensure_org` caveat (senior-review N3):** `ensure_org('bursar',…)` returns the existing id 17
  WITHOUT reconciling its `type`/parent against the call — they already match (verified `type='office'`,
  parent `njit`), so G1's "no rename" holds because the row is already correct, not because the code
  enforces it.

## Testing (TDD, mirrors the GSO suite) — `v2/tests/test_bursar_*.py` with real saved fixtures

- **T1 (the load-bearing anti-fab test — strengthened per RAG-review B1).** Two parts:
  (a) from the **real `contact-us` fixture**, assert `clean_text` contains no `"personnel"` token AND
  `parse_roster` returns `[]` AND the contact prose (emails/phone/hours) IS captured as a prose page;
  (b) **NEGATIVE regression:** feed a synthetic `personnel`-anchor + office-label + phone + *function*
  email arrangement and assert **no Person is emitted** (the function-email guard fires) — pins parser
  safety to a stray `"personnel"`, not to the site's current text. T1 and T7 are paired.
- **T2.** Prose cleaner → verbatim in/out (exact-mechanical-clean; no added words).
- **T3.** Discovery scope → from the **real homepage fixture**, the single seed reaches the **verified**
  key sections (`for-students`, `forms`, `faq`, `important-dates`, `touchnet-erefund`, `refunds`,
  `1098-t.php`, `contact-us`) and **stays on `/bursar`**; off-path/off-host dropped. (Does NOT assert
  `tuition-and-fee-schedule`/`faqs`/`for-parents` — those are not crawled in-scope pages, per recon.)
- **T4.** Classifier → a real service page (e.g. `for-students`)=`prose`; a **synthetic empty-HTML
  fixture**=`skip:js-shell` (no real bursar page skips — `schedule-appointment` is prose); a PDF link=
  `skip:pdf` (flagged, page still stored).
- **T5.** Change detection → unchanged page = no-op (no re-embed); changed page = re-embed.
- **T6.** Budget truncation → over-budget crawl sets the truncation flag.
- **T7.** Uniform-output → after ingest, Org `bursar` (id 17) under `njit` + KB items > 0; `verify_kg`
  passes; **people count == 0** (meaningful only paired with T1's negative test).
- **T8 (regression).** Re-run EOS + IST + GSO suites (`pytest -k "eos or ist or gradstudies"`) →
  unchanged green (the function-email guard lives in the bursar copy, so these stay green).

## Gated workflow (the HARD GATE)

1. This design → **RAG/anti-fab review + senior-eng review** (background agents, against the spec's
   stated goals) → fold findings → owner sign-off.
2. TDD build on branch `feat/bursar-crawl` → show the diff → owner sign-off.
3. **Gated live write:** dev copy first (`cp gsa_gateway.db /tmp/dev.db`;
   `crawl_bursar.py --db /tmp/dev.db --commit`; inspect + `verify_kg`), then live
   `crawl_bursar.py --commit --embed` (`hardened_backup('pre-bursar-crawl')` first). DB-only → no
   bot restart.
4. Verify by chat (use REAL pages — senior-review N4): "how do I set up eRefund", "bursar forms /
   1098-T", "how do I get a refund", "eStatements", "billing & refund deadlines", "how do I contact the
   bursar", "what is the financial responsibility agreement", "how do I schedule a bursar appointment".
   **Plus one deliberate deep-fact probe** (RAG-review N1): a fact known to live **past char 2000** in a
   long page (e.g. a specific item in the `faq`/`important-dates`/`touchnet-erefund` page) — to observe
   whether the M2 embed-truncation degrades that answer (FTS should still catch it). **Append these
   questions to `eval/questions.txt`** under a `# bursar` header (`feedback_grow_correctness_suite`).
5. **G7 clean-replace migration** (separate, gated, AFTER step 4 verifies): dry-run the exact retire
   list → `hardened_backup` → `--commit` → re-verify counts.
6. Merge `feat/bursar-crawl` → main after it's proven.

## Goals checklist (to verify at completion)

- [ ] G1 reuse `bursar` Org (id 17) under `njit` (idempotent, no recreate/rename/alias)
- [ ] G2 no fabricated people — 0 Person nodes; **function-email guard + negative test** (not bare assumption)
- [ ] G3 EVERY in-scope page (22) verbatim KB (`type='policy'`, served), embedded, RAG-answerable —
  **M2 embed-truncation flagged for the 11 long pages**
- [ ] G4 supervised manifest gate (CLI dry-run; 22 pages, real PDF count flagged)
- [ ] G5 recrawl content-hash diff (re-embed only changed)
- [ ] G6 uniform department-model output
- [ ] G7 clean-replace migration (separate gated step; retire 15 njit-crawl + the `type='contact'` stub
  **by source+natural_key**, anchored `njit.edu/bursar` matcher; evidence counts)
- [ ] **G5/ND6 departure reconciliation — DEFERRED-WITH-FLAG**
- Non-goals ND1–ND7 honored (ND5 PDF capability gap; **ND7 "student accounts" alias = recommended
  fast-follow**, separate gated task)
- **Hard-gate reviews folded (2026-06-24):** senior-eng (APPROVE-WITH-CHANGES: B1 recon→22 pages,
  B2 schedule-appointment not JS, B3 URL-matcher) + RAG/anti-fab (APPROVE-WITH-CHANGES: B1 function-email
  fabrication guard, N1 M2 truncation). All blocking items resolved in this spec; the M2 ship-now-vs-fix-
  first call is the owner decision below.
```
