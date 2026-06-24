# Graduate Studies Crawl — Design Spec

**Date:** 2026-06-24
**Author:** Mohammad Dindoost + Claude (brainstorm)
**Status:** Design approved; pending spec write → HARD-GATE reviews → build.

## Summary

Add NJIT **Office of Graduate Studies** (`www.njit.edu/graduatestudies/`, the "GSO") to the DB under the
**uniform department model** so the bot answers everyday grad-student questions: PhD/MS credit + full-time
status, PhD-milestone timeline, thesis/dissertation submission & approval, forms, new-student orientation,
the current-students FAQ, professional development, GSA travel awards, academic-integrity policy, and the
GSO's contacts/leadership.

This applies the **EOS/IST crawler lesson** (`v2/core/ingestion/eos_crawl.py`, `ist_crawl.py`, both shipped
2026-06-24): seed a landing page, scoped DFS, store **verbatim prose as `type='policy'`** in the served
corpus (no staging, no decline branch), capture staff into the KG, recrawl = content-hash diff. GSO is just
another NJIT office. It becomes the **single repeatable source** for `/graduatestudies/`, replacing today's
63 hodgepodge rows (see Existing-state cleanup).

**Build stance (owner's call, same as IST):** build a **separate** GSO crawler now — a **copy of the EOS
crawler**, adapted — so the proven EOS/IST crawlers are never destabilized. Converge EOS + IST + GSO into one
config-driven engine later (a separately-gated refactor, ND1).

## The coverage rule this design obeys (Mohammad, 2026-06-24)

**Whatever is on the website within the entry point, we grab — verbatim, no content exclusions, ever.** The
only boundary is the entry point itself (the `/graduatestudies/` path-prefix); staying on it is *what the
entry point is*, not curation. There is **no "exclude this page" decision** anywhere in this design. PDF is
the one different category and it is a **capability gap, not a content judgment**: we cannot extract PDF
bytes yet (deferred pypdf TODO, see ND5), so a PDF *page* is still stored verbatim **with its PDF link**
(info reachable, just not yet text-searchable) — "can't yet," never "chose not to." Info is non-negotiable
unless there is a specific, verified, valid reason. See memory `feedback_crawler_complete_coverage` +
the CLAUDE.md hard line "NJIT web content is served VERBATIM, never withheld."

## Context & prior art

- **Uniform department model:** every NJIT unit = one `type='Org'` node (`part_of` its parent) + people as
  `Person` nodes with `has_role` edges (carrying `category` + `attrs.titles` + contact) + prose as
  `knowledge_items` tagged via `org_id`. GSO slots into this identical model. Memory
  `project_dept_uniform_ingest_standard`.
- **EOS crawler (the model to copy — closer fit than IST):** EOS's `_in_scope` is a **per-seed path-prefix**
  bound, and its roster parser reads **inline phone/email** contacts. GSO is `www.njit.edu/graduatestudies/`
  (a path prefix under `www.njit.edu`) and its `contact.php` lists staff **with inline email/phone** — so
  EOS is the right template on both axes (IST needed host-match scope + a no-inline-contact roster; GSO does
  not).
- **GSO web shape (recon 2026-06-24, read-only — no DB writes):**
  - **Canonical host = `www.njit.edu/graduatestudies/`** (HTTP 200). The `graduatestudies.njit.edu` subdomain
    is **dead** (no response); the DB stub id 131 pointing at it is stale.
  - Homepage (~70 KB) exposes **34 in-scope `/graduatestudies/` sections at depth 1** — incl.
    `new-phd-credit-requirements`, `detailed-graphicchart-showing-timeline-phd-program-milestones`,
    `current-students`, `current-students/thesis-dissertation-submission-approval`, `degree-programs`,
    `forms`, `full-time-status-phd-students`, `full-time-status-ms-students`, `graduate-faculty`,
    `graduate-professional-development`, `graduate-studies-faq-current-students`, `gsa-travel-awards`,
    `dos/academic-integrity`, `office-graduate-studies`, `content/office-vice-provost-graduate-studies`,
    `fall-2025-new-domestic-graduate-student-orientation`, etc. A single homepage seed reaches the site.
  - **`contact.php` Personnel section** lists named staff **with inline email + phone**, e.g. *Sotirios G.
    Ziavras, D.Sc. — Vice Provost for Graduate Studies and Dean of the Graduate Faculty — 973-596-3462 —
    ziavras@njit.edu*, *Clarisa González-Lenah — …*. Office email `graduatestudies@njit.edu`, phone
    (973) 596-3462, Fenster Hall Suite 140.
  - **`graduate-faculty`** is GSO **policy prose** ("For Graduate Faculty": CGE voting/nonvoting membership,
    faculty guidelines, Credit-by-Exam, program approvals via CIM) — **not** a person roster. It is normal
    in-scope KB content and is captured like any other page. (Recorded because an earlier assumption that it
    was an all-faculty roster to exclude was wrong; corrected by recon. No page is excluded.)
  - **PDFs:** few on the homepage; `/forms` links PDF forms; the milestone timeline may be a graphic/PDF.
- **Existing DB state (verified):** `organizations` row **id 9** (`slug='graduate-studies'`, name
  `'Graduate Studies'`, `type='office'`, parent `njit`). **63 active `knowledge_items` tagged to org 9** from
  mixed sources: 1 `migration` (id 131, the dead subdomain stub), several `dashboard` (incl. **4 duplicate**
  "Ph.D. Credit Requirements" rows 4472–4475 + the contact stub 4393), and ~57 `njit-crawl` (the older
  one-off grounded pass, titled "Office of Graduate Studies — …"). 0 GSO people in the KG.

## Goals

- G1. GSO exists as the **existing** `Org` (id 9, `slug='graduate-studies'`, `type='office'`, parent `njit`) —
  reused idempotently via `ensure_org`, not recreated. No new org, no rename, no alias.
- G2. GSO Personnel (`contact.php`) are `Person` nodes with `has_role` edges to `graduate-studies`
  (`category='staff'`, contact = the **published** phone/email — captured, never invented). Capture all found;
  unparseable rows flagged for owner, never dropped silently, never fabricated.
- G3. **Every** in-scope `/graduatestudies/` page is a verbatim `knowledge_item` **`type='policy'`** tagged to
  `graduate-studies`, embedded, and answerable via the normal RAG path. No content exclusions.
- G4. A **supervised first crawl**: a dry manifest (URL + proposed type + extraction preview + flagged PDFs/
  unknowns) the owner reviews before any live write.
- G5. **Recrawl**: re-walk the saved URL set, content-hash diff, re-embed only changed pages.
- G6. Output conforms to the uniform department model, so the future converged engine generalizes GSO without
  loss.
- G7. **Clean replace (separate gated migration):** after the new crawler content is verified, retire the
  stale `migration` (id 131) + `njit-crawl` (~57) rows and dedup the 4 `dashboard` Ph.D. rows, **keeping** any
  genuinely manual `dashboard` item not present on the live site. GSO ends with one clean, repeatable source.

## Non-goals (explicit deferrals)

- ND1. **No shared/unified crawl engine yet** — GSO is a *separate copy* of the EOS crawler now; converge
  EOS + IST + GSO into one config-driven engine only after GSO is proven in production.
- ND2. **No off-path / off-host crawling** — the scope is the `/graduatestudies/` path-prefix on
  `www.njit.edu` only. Off-path `www.njit.edu` sections and off-host links (people.njit.edu profiles, external
  apps) are outside the single entry point; their links survive verbatim in the prose and the live-fallback
  covers them on demand. **This is the entry-point boundary, not a content exclusion.**
- ND3. **No staging / decline / office_page tier** — GSO prose is normal KB (`type='policy'`), served by the
  existing RAG path.
- ND4. **No JS rendering** — JS-only shells empty after clean → `skip:js-shell`, flagged, not faked. (A
  flagged empty page is a fetch/capability outcome, not a content exclusion — it is surfaced, never silently
  dropped.)
- ND5. **No PDF text extraction** — a capability gap, not a content judgment. The PDF *page* is stored
  verbatim with its PDF link (info reachable); the PDF *bytes* are flagged in the manifest, not extracted.
  See `project_pdf_extraction_todo` (deferred pypdf, needs dep approval). The owner may later choose to
  hand-grab specific high-value PDFs (forms, milestone chart) into KB.
- ND6. **No departure reconciliation in this build** (G5 is change-detection only) — see checklist.
- ND7. **No org aliases for GSO** — do not add topic↔acronym aliases to `_ORG_ALIASES`/`metadata.aliases`;
  acronym↔topic links live in page CONTENT.

## Architecture

New GSO-specific module `v2/core/ingestion/gradstudies_crawl.py` (a copy of `eos_crawl.py`, adapted) + a
gated CLI `scripts/crawl_gradstudies.py` (a copy of `crawl_eos.py`). EOS/IST files are **not modified** → zero
regression risk. Components:

1. **Discovery (DFS, path-prefix-scoped)** — seed `https://www.njit.edu/graduatestudies/`; DFS following
   same-host links whose path starts with `/graduatestudies` (EOS's `_in_scope` path-prefix model). Drops
   off-path/off-host + asset links. Depth + page-budget bounded, with a **truncation flag** on the result +
   manifest. Recon confirmed 34 sections reachable from the single homepage seed.
2. **Page-type classifier** — per URL a proposed type: `staff-roster` (contact.php Personnel), `prose`
   (extractable text), `skip:js-shell` (empty after clean), `skip:pdf`/`skip:asset`, `unknown`. Deterministic;
   `unknown` and `skip:*` are **flagged in the manifest**, never silently dropped, never guessed. (Single
   parse per page, per the EOS fix.)
3. **Manifest (dry run)** — candidate URL set + types + extraction previews + flagged PDFs/unknowns to stdout.
   No DB writes. The supervised gate.
4. **Personnel parser (EOS-style, inline contact)** — parse `contact.php` Personnel section: each record =
   name + title line(s) + inline **phone/email** block. Emit `project_appointment` (Person + `has_role`
   `category='staff'`, `attrs.titles`, contact = the published phone/email). Ziavras (Vice Provost/Dean) +
   the rest of the GSO staff. A record that doesn't parse cleanly → **warn + ask owner**, never dropped, never
   fabricated. **Coverage-rule deviation from EOS:** EOS roster pages were pure rosters, so EOS lets a roster
   page's prose be dropped (roster-precedence). `contact.php` ALSO carries genuine office-contact prose the
   office email `graduatestudies@njit.edu`, phone (973) 596-3462, Fenster Hall Suite 140, hours, and the
   GSO-appointment-request instructions — which grad students need. Per "grab everything," `contact.php`
   yields **both** the KG staff **and** its page prose as a `type='policy'` KB item; roster-precedence must
   NOT drop it.
5. **Prose ingester** — mechanically clean each in-scope page to verbatim text → `knowledge_items`
   (`type='policy'`, `org_id=graduate-studies`), idempotent on a full-path natural key, content-hash for
   recrawl diff, figures/files (incl. PDF links) in `metadata`.
6. **Recrawl** — re-walk saved URLs, content-hash diff, re-embed only changed pages (departure reconciliation
   deferred — ND6 / checklist).

### Data flow

**First crawl (supervised):**
```
seed www.njit.edu/graduatestudies/ → path-scoped DFS → classify → MANIFEST (dry, no writes)
   → owner reviews in chat
   → gated live write: ensure_org(graduate-studies, existing id 9) + contact.php→people + every page→KB(policy)
   → embed_all → verify_kg + counts (evidence) → owner confirms
```
**Clean replace (G7, separate gated migration, AFTER verify):**
```
dry-run listing the exact rows to retire (migration id 131 + njit-crawl ~57 + dup dashboard Ph.D. rows;
keep manual-only dashboard rows) → hardened_backup → --commit → re-verify counts
```
**Recrawl (unsupervised):**
```
re-walk saved URLs → content-hash diff → re-embed changed → report (no departure retire — ND6)
```

### Output model (uniform)

- `nodes`: reuse Org `graduate-studies` (id 9) + N new Person (staff). Edges: each staff `--has_role-->
  graduate-studies` (`category='staff'`, `attrs.titles=[...]`, contact in Person attrs).
- `knowledge_items`: one per GSO page, `org_id=graduate-studies`, `type='policy'`, verbatim content, embedded.
- `created_by='crawler'`. **Reconcile is source-scoped:** the crawler only ever touches `source='crawler'`
  rows, so the 63 existing rows (migration/dashboard/njit-crawl) are never touched by the crawl itself —
  retiring them is the **separate** G7 migration (a curation decision, which by the hard line must live
  outside the crawler).

## Error handling / anti-fabrication

- Fetch errors / non-200 → flag in manifest, skip (no partial fabricated content).
- JS-shell empty-clean → `skip:js-shell`, flagged, never faked.
- `unknown` page type → flagged, never guessed into a type.
- Personnel parse: emit a Person from each name/title/contact record; a record that doesn't parse cleanly →
  **warn + ask owner**, never dropped, never invented (capture the published phone/email; never fabricate a
  missing one).
- Verbatim guarantee: prose stored == mechanically-cleaned page text; a test asserts no rewriting
  (exact-mechanical-clean / token-subsequence).
- Off-path/off-host wandering: a test asserts DFS never leaves `/graduatestudies/`.

## Testing (TDD, mirrors the EOS/IST suites)

New `v2/tests/test_gradstudies_*.py` with **real saved `graduatestudies` HTML fixtures**:
- T1. Personnel parser → GSO staff (name/title/**phone/email**) from the real `contact.php` fixture;
  unparseable rows surfaced as warnings, not dropped.
- T2. Prose cleaner → verbatim in/out (no added words; exact-mechanical-clean).
- T3. Discovery scope → from the **real homepage fixture**, the single seed reaches every key section
  (`new-phd-credit-requirements`, `forms`, `current-students`, `degree-programs`, `graduate-faculty`,
  `full-time-status-phd-students`, …) and **stays on `/graduatestudies/`**; off-path/off-host dropped.
  (Closes the IST review's "real-homepage discovery untested" finding from the start.)
- T4. Classifier → contact.php=`staff-roster`, a service page=`prose`, an empty shell=`skip:js-shell`, a
  PDF link=`skip:pdf` (flagged, page still stored).
- T5. Change detection → unchanged page = no-op (no re-embed); changed page = re-embed.
- T6. Budget truncation → over-budget crawl sets the truncation flag.
- T7. Uniform-output → after ingest, Org `graduate-studies` (id 9) under `njit` + people-with-roles + KB items
  > 0; `verify_kg` passes.
- T8 (regression). Re-run the full EOS **and** IST suites (`pytest -k "eos or ist"`) → unchanged green.

## Gated workflow (the HARD GATE)

1. This design → **RAG/anti-fab review + senior-eng review** (background agents, against the spec's stated
   goals) → fold findings → owner sign-off.
2. TDD build on branch `feat/gradstudies-crawl` → show the diff → owner sign-off.
3. **Gated live write:** dev copy first (`cp gsa_gateway.db /tmp/dev.db`;
   `crawl_gradstudies.py --db /tmp/dev.db --commit`; inspect + `verify_kg`), then live
   `crawl_gradstudies.py --commit --embed` (`hardened_backup('pre-gradstudies-crawl')` first). DB-only → no
   bot restart.
4. Verify by chat: "PhD credit requirements", "how do I submit my thesis", "graduate studies forms",
   "full-time status PhD", "new graduate student orientation", "who is the Vice Provost for Graduate Studies".
5. **G7 clean-replace migration** (separate, gated, AFTER step 4 verifies): dry-run the exact retire list →
   `hardened_backup` → `--commit` → re-verify counts.
6. Merge `feat/gradstudies-crawl` → main after it's proven.

## Goals checklist (to verify at completion)

- [ ] G1 reuse `graduate-studies` Org (id 9) under `njit` (idempotent, no recreate/rename/alias)
- [ ] G2 Personnel → people w/ roles + published contact (all found; ambiguous → owner; never fabricated)
- [ ] G3 EVERY in-scope page verbatim KB (`type='policy'`, served), embedded, RAG-answerable — no exclusions
- [ ] G4 supervised manifest gate (CLI dry-run, PDFs/unknowns flagged)
- [ ] G5 recrawl content-hash diff (re-embed only changed)
- [ ] G6 uniform department-model output
- [ ] G7 clean-replace migration (separate gated step; retire stale/dup, keep manual-only; evidence counts)
- [ ] **G5/ND6 departure reconciliation — DEFERRED-WITH-FLAG** (retiring removed pages/departed staff is NOT
  built; safe for the FIRST crawl; do NOT advertise as a repeatable recrawl until built — reuse
  `explore.py reconcile_departures` keyed on `metadata.natural_key`). [[project_ist_crawl_build]]
- Non-goals ND1–ND7 honored (ND5 PDF = capability gap, page+link still captured, future TODO
  `project_pdf_extraction_todo`; convergence ND1 deferred to a later gated refactor).
