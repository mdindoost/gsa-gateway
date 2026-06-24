# IST Crawl — Design Spec

**Date:** 2026-06-24
**Author:** Mohammad Dindoost + Claude (brainstorm)
**Status:** Design approved; pending spec review → implementation plan → HARD-GATE reviews → build.

## Summary

Add NJIT **Information Services & Technology** (`ist.njit.edu`, hereafter **IST**) to the DB so the
bot can answer everyday student IT questions: UCID/password reset, wifi, software downloads, email,
Canvas, student computers, and the IST Service Desk's hours/contact. Today IST is a near-empty org —
**one** manual contact stub (`knowledge_items` id 4394) and **zero** people.

This applies the **EOS `/parking/` crawler lesson** (`v2/core/ingestion/eos_crawl.py`, shipped
2026-06-24): seed a landing page, DFS within scope, store **verbatim prose as `type='policy'`** in the
served corpus (no `office_page` tier, no staging, no decline branch), capture staff into the KG, and
make recrawl a content-hash diff. IST is just another NJIT office under the **uniform department model**.

**Build stance (owner's call):** build a **separate** IST crawler now — a **copy** of the EOS crawler,
adapted as needed — so the proven EOS crawler is never destabilized. Once IST is verified perfect in
production, **converge** EOS + IST into one config-driven engine (a later, separately-gated refactor).

## Context & prior art

- **Uniform department model:** every NJIT unit = one `type='Org'` node (`part_of` its parent) + people
  as `Person` nodes with `has_role` edges (carrying `category` + `attrs.titles` + contact) + prose as
  `knowledge_items` tagged via `org_id`. IST slots into this identical model. See memory
  `project_dept_uniform_ingest_standard`.
- **EOS crawler (the model to copy):** `eos_crawl.py` — scoped DFS from seed(s) (`_in_scope`),
  verbatim prose extractor (+figure/img/asset capture), page classifier (roster/prose/skip-empty),
  https-canon + content-hash dedup (`_canon`, collapses `.php`/http aliases), recurring-asset stripper
  (near-universal threshold `k≥n-1 & n≥5`), `ingest_eos` (idempotent on natural key, content-hash
  recrawl diff), gated CLI `crawl_eos.py` (dry-run default, `hardened_backup` + `--commit` + `--embed`).
- **IST web shape (recon 2026-06-24, `ist.njit.edu`):** a single cohesive subdomain (NOT the
  `people.njit.edu/profile/<slug>` template, so `njit_adapter` does not apply — IST needs its own
  prose + Key-Contacts parsers, exactly as EOS did). Main sections: `/ist-services` (Services A–Z),
  `/software-available-download`, `/about-ist` (Key Contacts, Service Desk, Support Services),
  `/i-am` (role-based incl. Student), `/ist-service-desk`, `/password-resets`, `/student-computers`.
  External app links: `myucid.njit.edu` (password), `servicedesk.njit.edu` (ticketing + KB articles
  like wifi `KB0010086`) — different hosts, out of scope (see ND2/limitations).
- **Existing DB state:** `organizations` row id 22 (`slug='ist'`, name `'IST / Technology Support'`,
  `type='office'`, parent `njit`); one manual contact KB stub id 4394 (`created_by='dashboard'`,
  `source_url=https://ist.njit.edu/ist-service-desk`); 0 IST people.

## The three 2026-06-23 HARD LINES this design obeys

1. **NJIT web content is served VERBATIM, never withheld** — IST prose stored and served as-is; no
   staging (`is_active=0`), no decline/redaction. Source link + heads-up cover staleness.
2. **Evidence before any state claim** — every count/assertion about what was written is verified
   (row counts, checksums, `verify_kg`), never asserted from memory.
3. **Crawl/recrawl brings data ONLY** — the extractor fetches → mechanically cleans → stores to KB/KG.
   It makes NO serving/gating/staging/decline decisions. Cleaning is mechanical (strip markup/
   boilerplate/control chars, fix whitespace); human-readable text passes through unchanged.

## Goals

- G1. IST exists as the **existing** `Org` (id 22, `slug='ist'`, `type='office'`, parent `njit`) —
  reused idempotently via `ensure_org`, not recreated. No new org, no rename, no alias.
- G2. IST Key Contacts (`/about-ist`) are `Person` nodes with `has_role` edges to `ist`
  (`category='staff'`, contact = phone/email). **Capture all found**; ambiguous contacts flagged for
  owner confirmation, never invented.
- G3. IST service/support pages (UCID/password, wifi, software, email, Canvas, student computers,
  service desk, and the rest the site links) are verbatim `knowledge_items` **type='policy'** tagged
  to `ist`, embedded, and answerable via the normal RAG path.
- G4. A **supervised first crawl**: a dry manifest (URL + proposed type + extraction preview) the owner
  reviews before any live write; unknowns/JS-shells/PDFs flagged, not guessed.
- G5. **Recrawl**: re-walk the saved URL set, content-hash diff (re-embed only changed pages).
- G6. Output conforms to the uniform department model, so the future converged engine generalizes IST
  without loss.

## Non-goals (explicit deferrals)

- ND1. **No shared/unified crawl engine yet** — IST is a *separate copy* of the EOS crawler now;
  converge EOS + IST into one config-driven engine only after IST is proven perfect in production.
- ND2. **No off-host / app-subdomain crawling** — `myucid.njit.edu`, `servicedesk.njit.edu`, and
  Service-Desk KB articles (e.g. wifi `KB0010086`) are out of scope (apps / likely auth-walled). Their
  links survive verbatim in the prose ("reset at myucid.njit.edu", "see KB0010086"); the live-fallback
  covers the rest on demand.
- ND3. **No `office_page` tier / staging / decline** — the dormant office machinery stays untouched.
  IST prose is normal KB (`type='policy'`), served by the existing RAG path.
- ND4. **No JS rendering** — JS-only shells empty after clean → `skip:js-shell`, flagged, not faked.
- ND5. **No PDF extraction** — PDFs skipped + flagged ([[project_pdf_extraction_todo]]).
- ND6. **No departure reconciliation in this build** (G5 is change-detection only) — see checklist.
- ND7. **No org aliases for IST** — do not add IST↔"wifi"/"password" to `_ORG_ALIASES`/`metadata.aliases`
  (avoids a topic query dead-ending in `people_in_org`); acronym↔topic links live in page CONTENT.

## Architecture

New IST-specific module `v2/core/ingestion/ist_crawl.py` (a copy of `eos_crawl.py`, adapted) + a gated
CLI `scripts/crawl_ist.py` (a copy of `crawl_eos.py`). EOS files are **not modified** → zero regression
risk to the shipped EOS crawler. Components:

1. **Discovery (DFS, host-scoped)** — seed `https://ist.njit.edu/`; DFS following same-host links only
   (stays on `ist.njit.edu`; drops off-host `www`/`myucid`/`servicedesk`/external + CSS/JS/PDF/asset
   links). Depth + page-budget bound, with a **truncation flag** on the result + manifest (the EOS
   review fix #5). **Adaptation vs EOS (PILOT-CONFIRMED):** EOS's `_in_scope` is a per-seed path-prefix
   bound (5 separate sites under `www.njit.edu`) — the pilot showed it rejects IST's sibling links (a
   `/software-available-download` seed followed 0 links). Replace it with a **host-match** bound: seed the
   homepage, follow any `ist.njit.edu` link. The pilot confirmed `select_links` already host-bounds (18
   on-host / **0 off-host** from the homepage), and the homepage exposes every key section at depth 1
   (`/software-available-download`, `/password-resets`, `/student-computers`, `/ist-services`,
   `/ist-key-contacts`, `/popular-services`, `/new-student-computing-guide`, …) — so a single homepage
   seed reaches the site; no per-section seeds needed.
2. **Page-type classifier** — per URL a proposed type: `staff-roster` (Key Contacts), `prose-page`
   (extractable text), `skip:js-shell` (empty after clean), `skip:pdf`/`skip:asset`, `unknown`.
   Deterministic; `unknown` never guessed. (Single-parse, per the EOS fix — no classify double-parse.)
3. **Manifest (dry run)** — candidate URL set + types + extraction previews to stdout. No DB writes. The
   supervised gate.
4. **Key-Contacts parser (PILOT-CONFIRMED structure)** — parses **`/ist-key-contacts`** (NOT `/about-ist`,
   which is the division overview). Real structure per the pilot: people grouped under **unit headers**
   (Office of the VP / Digital Learning & Campus Support / Enterprise Applications / Research IT / …), each
   record = `Lastname, Firstname` → `Title` → `View Profile` (the **record delimiter** — replaces EOS's
   email-line delimiter). **No inline phone/email** on this page (contact lives behind the `View Profile`
   link to `people.njit.edu`). So the parser: split on `View Profile`, take name (reformat
   `Last, First`→`First Last`) + title, capture the unit header as `attrs.titles`/source_section context;
   emit `project_appointment` (Person + `has_role` `category='staff'`) under `ist` with **no fabricated
   contact** (anti-fab — omit phone/email rather than invent). ~18 contacts (Haggerty=Interim VP,
   Farber=Asst Director Service Desk, unit directors). Optional enrichment (open question): follow
   `View Profile` to the `people.njit.edu/profile/<slug>` page (the standard template `njit_adapter`
   already parses) to pull email — deferred unless the owner wants it.
5. **Prose ingester** — mechanically cleans each prose page to verbatim text → `knowledge_items`
   (`type='policy'`, `org_id=ist`), idempotent on a full-path natural key (avoids doc_id collisions),
   figures/files in `metadata`.
6. **Recrawl** — re-walk saved URLs, content-hash diff, re-embed only changed pages (departure
   reconciliation deferred — ND6 / checklist).

### Data flow

**First crawl (supervised):**
```
seed ist.njit.edu/ → host-scoped DFS → classify → MANIFEST (dry, no writes)
   → owner reviews in chat
   → gated live write: ensure_org(ist, existing id 22) + KeyContacts→people + prose→KB(type=policy)
   → embed_all → verify_kg + counts (evidence) → owner confirms
```
**Recrawl (unsupervised):**
```
re-walk saved URLs → content-hash diff → re-embed changed → report (no departure retire — ND6)
```

### Output model (uniform)

- `nodes`: reuse Org `ist` (id 22) + N new Person (staff). Edges: each staff `--has_role--> ist`
  (`category='staff'`, `attrs.titles=[...]`, contact in Person attrs).
- `knowledge_items`: one per IST page, `org_id=ist`, `type='policy'`, verbatim content, embedded.
- `created_by='crawler'`. **Reconcile is source-scoped:** the existing manual stub id 4394
  (`created_by='dashboard'`) is **never touched**; manual and crawled rows coexist (different
  `created_by`; the service-desk page may appear as both a manual `contact` and a crawled `policy` doc —
  both serve, accepted).

## Error handling / anti-fabrication

- Fetch errors / non-200 → flag in manifest, skip (no partial fabricated content).
- JS-shell empty-clean → `skip:js-shell`, flagged, never faked.
- `unknown` page type → flagged, never guessed into a type.
- Key-Contacts parse (anchored on the `View Profile` delimiter): emit a Person from the name + title lines
  immediately above each delimiter; a delimiter whose lines don't parse cleanly → **warn + ask owner**,
  never silently dropped and never invented (no fabricated phone/email — the page has none).
- Verbatim guarantee: prose stored == mechanically-cleaned page text; a test asserts no rewriting
  (exact-mechanical-clean / token-subsequence).
- Off-host wandering: a test asserts DFS never leaves `ist.njit.edu`.

## Testing (TDD, mirrors the EOS suite)

New `v2/tests/test_ist_*.py` with **real saved `ist.njit.edu` HTML fixtures**:
- T1. Key-Contacts parser → the IST contacts (name/title/**unit**, NO phone/email) from the
  **`/ist-key-contacts`** fixture, anchored on the `View Profile` delimiter; unparseable rows surfaced as
  warnings, not dropped.
- T2. Prose cleaner → verbatim in/out (no added words; exact-mechanical-clean).
- T3. Discovery scope → stays on `ist.njit.edu`; drops off-host (`www`/`myucid`/`servicedesk`/external)
  + CSS/JS/PDF/asset links.
- T4. Classifier → contacts page=`staff-roster`, a service page=`prose-page`, an empty shell=`skip:js-shell`.
- T5. Change detection → unchanged page = no-op (no re-embed); changed page = re-embed.
- T6. Budget truncation → over-budget crawl sets the truncation flag.
- T7. Uniform-output → after ingest, Org `ist` (id 22) under `njit` + people-with-roles + KB items > 0,
  matching the department-model shape; `verify_kg` passes.
- **T8 (regression). Re-run the full EOS suite** (`pytest -k eos`) → unchanged green (the copy must not
  disturb EOS).

## Gated workflow (the HARD GATE)

1. This design → **RAG/anti-fab review + senior-eng review** (background agents, against the spec's
   stated goals) → fold findings → owner sign-off.
2. TDD build on branch `feat/ist-crawl` → show the diff → owner sign-off.
3. **Gated live write:** dev copy first (`cp gsa_gateway.db /tmp/dev.db`;
   `crawl_ist.py --db /tmp/dev.db --commit`; inspect + `verify_kg`), then live
   `crawl_ist.py --commit --embed` (`hardened_backup('pre-ist-crawl')` first). **DB-only → no bot restart.**
4. Verify by chat: "how do I reset my UCID password", "NJIT wifi", "IST service desk hours",
   "what software can I download", "who runs IST".
5. Merge `feat/ist-crawl` → main after it's proven.

## Pilot findings (2026-06-24, read-only — no DB writes)

Ran the EOS extractor (`eos_crawl.extract_entry`) against `ist.njit.edu`, validating feasibility:
- **Prose extraction transfers as-is** — clean verbatim text, correct titles, no off-host leak, no
  rewriting (Software Availability 3050ch, IST Services for Students 2372ch, Acceptable Use Policy
  11595ch, Service Desk 881ch, etc.). The verbatim hard line holds.
- **Scope fix identified + validated** — per-section seed followed 0 links (path-prefix `_in_scope` too
  narrow); homepage seed + host-bound walked 12 pages in a shallow depth-2/budget-35 pass (`truncated`,
  more with budget); `select_links` host-bounds already (0 off-host). → host-scope, homepage seed.
- **Key-Contacts structure captured** (see component 4) — `/ist-key-contacts`, `Last, First / Title /
  View Profile`, no inline contact. Roster parser is a real rewrite, not an anchor tweak.

## Open questions / to confirm during the supervised crawl

- Final page budget/depth for full coverage (shallow pilot already hit `truncated=True`; the real crawl
  needs a budget sized to the whole subdomain — set it, then confirm no truncation in the manifest).
- **Enrich staff email by following `View Profile` → `people.njit.edu`?** (Optional; deferred unless owner
  wants it — otherwise staff are name+title+unit only, no fabricated contact.)
- Whether any high-value step-by-step (wifi/password) exists ONLY as a Service-Desk KB article (off-host,
  ND2) → accept the link-out + live-fallback, or flag for a manual anchor doc.

## Goals checklist (to verify at completion)

- [ ] G1 reuse `ist` Org (id 22) under `njit` (idempotent, no recreate/rename/alias)
- [ ] G2 Key Contacts → people w/ roles + contact (all found; ambiguous → owner)
- [ ] G3 service pages verbatim KB (`type='policy'`, served), embedded, RAG-answerable
- [ ] G4 supervised manifest gate (CLI dry-run)
- [ ] G5 recrawl content-hash diff (re-embed only changed)
- [ ] **G5/ND6 departure reconciliation — DEFERRED-WITH-FLAG** (retiring removed pages/departed staff is
  NOT built; safe for the FIRST crawl; do NOT advertise as a repeatable recrawl until built — reuse
  `explore.py reconcile_departures` keyed on `metadata.natural_key`). [[project_eos_crawl_build]]
- [ ] G6 uniform department-model output
- Non-goals ND1–ND7 honored (ND5 PDF-skip = separate future TODO [[project_pdf_extraction_todo]];
  convergence ND1 deferred to a later gated refactor).
