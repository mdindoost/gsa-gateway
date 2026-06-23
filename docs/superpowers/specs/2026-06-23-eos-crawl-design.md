# EOS Crawl — Design Spec

**Date:** 2026-06-23
**Author:** Mohammad Dindoost + Claude (brainstorm)
**Status:** Design approved; pending spec review → implementation plan → HARD-GATE reviews → build.

## Summary

Add the NJIT **Environmental & Operational Services** department — formally
*"Facilities, Systems, Photo Identification and Parking Services Department"* (hereafter **EOS**) —
to the DB as a **normal NJIT department**, using an **EOS-specific extractor modeled on the YWCC
crawler** (`v2/core/ingestion/explore.py`). Its **staff** go into the KG (people + roles + contact);
its **service pages** go into the KB (verbatim prose). Generalization into a reusable engine is
**deferred** until 2–3 departments have been done this way (rule of three).

This work follows the office-tier ROLLBACK of 2026-06-23. The lesson from that rollback drives the
core simplification here: **EOS is just another department**, so it needs **no `office_page` tier, no
staging, no decline branch** — the deviations that caused the rollback. Its prose is served by the
existing RAG path exactly like every faculty department's prose.

## Context & prior art

- **Uniform department model (verified 2026-06-23):** every NJIT unit = one `type='Org'` node
  (`part_of` its parent) + people as `Person` nodes with a `has_role` edge (carrying `category` +
  `attrs.titles` + contact) + prose as `knowledge_items` tagged via `org_id`. Counts: computer-science
  71 people/1890 KB, informatics 57/1108, mtsm 46/812, data-science 32/919. EOS slots into this
  identical model. See memory `project_dept_uniform_ingest_standard`.
- **Reusable spine — `explore.py`:** frontier (discover + queue links), `ensure_org`,
  `project_appointment` (Person + `has_role`), content-hash change detection (`_unchanged`),
  `reconcile_departures` (recrawl turnover). EOS reuses this spine.
- **EOS-specific = entry point + parser only:** EOS pages are NOT the `people.njit.edu/profile/<slug>`
  template, so the `njit_adapter` profile parser does not apply. EOS needs its own roster + prose parsers.
- **Prior recon (branch `feat/eos-parking-knowledge`, `findings/2026-06-22-eos-parking-crawl-notes.md`):**
  the EOS hub `https://www.njit.edu/parking/` is a separate Drupal multisite, ABSENT from NJIT sitemaps
  → reachable only by **seeding the hub + following its links**. ~33 `/parking/*` pages + off-hub
  service pages (`/mailroom/`, `/sustainability/`, `/environmentalsafety`, `/about/transportation-campus`).
  Gotchas: drop `.css?delta=` asset links; JS-only SPA shells (SchoolDude work-order portal, parking
  app) return empty → skip + flag, never fake; PDFs out of scope.

## The three 2026-06-23 HARD LINES this design obeys

1. **NJIT web content is served VERBATIM, never withheld** — service prose stored and served as-is; no
   staging (`is_active=0`), no decline/redaction. Source link + heads-up cover staleness.
2. **Evidence before any state claim** — every count/assertion about what was written is verified
   (row counts, checksums, `verify_kg`), never asserted from memory.
3. **Crawl/recrawl brings data ONLY** — the extractor fetches → mechanically cleans → stores to KB/KG.
   It makes NO serving/gating/staging/decline decisions. Cleaning is mechanical (strip markup/boilerplate/
   control chars, fix whitespace); the human-readable text passes through unchanged (no summarizing,
   paraphrasing, truncating, or content selection).

## Goals

- G1. EOS exists as one `Org` (`key='eos'`, `org_type='office'`, parent `njit`) with title + location.
- G2. EOS staff (Gjini=AVP, Mendez=Manager, Dabrowski/Guillen/Erixson=Coordinators, + any others found)
  are `Person` nodes with `has_role` edges to `eos`, carrying phone + email.
- G3. EOS service pages (parking, photo-id, visitor-parking, transportation, security, locksmith,
  mailroom, EHS, sustainability, and the rest the hub links) are verbatim `knowledge_items` tagged to
  `eos`, embedded, and answerable via the normal RAG path.
- G4. A **supervised first crawl**: a dry manifest (URL + proposed type + extraction preview) the owner
  reviews/corrects before any live write; unknowns/JS-shells flagged not guessed.
- G5. **Recrawl**: re-walk the saved URL set, content-hash diff (re-embed only changed), reconcile
  new/departed pages — reusing `explore.py` mechanisms.
- G6. Output conforms to the uniform department model so a future engine can generalize EOS without loss.

## Non-goals (explicit deferrals)

- ND1. **No general/unified crawl engine** — EOS-specific code now; unify after 2–3 departments.
- ND2. **No `office_page` tier / staging / decline** — the dormant office machinery stays untouched and
  unused. EOS prose is normal KB.
- ND3. **No JS rendering** — JS-only SPA shells are skipped + flagged, not rendered.
- ND4. **No PDF extraction.**
- ND5. **No dashboard crawl-review UI** — first-crawl review is a manifest file + chat. UI is a later idea.
- ND6. **No org aliases for EOS** — do not add EOS↔"parking" to `_ORG_ALIASES`/`metadata.aliases` (avoids
  "who works in parking" dead-ending in `people_in_org`); acronym↔phrase links live in page CONTENT.

## Architecture

EOS-specific module(s) under `v2/core/ingestion/` (e.g. `eos_crawl.py`) + a gated CLI
(`scripts/crawl_eos.py`). Components:

1. **Discovery** — seed `https://www.njit.edu/parking/`; follow same-host links + the off-hub service
   prefixes; drop CSS/JS/PDF/asset links. Produces a candidate URL set. (May reuse the worktree
   `select_seed_links` logic from the eos-parking branch.)
2. **Page-type classifier** — per URL, a proposed type: `staff-roster` (the contacts page),
   `prose-page` (service pages with extractable text), `skip:js-shell` (empty after clean),
   `skip:pdf`/`skip:asset`, `unknown`. Deterministic rules; `unknown` never guessed.
3. **Manifest (dry run)** — writes the candidate set + types + extraction previews to a review file.
   No DB writes. The supervised gate.
4. **Roster parser** — parses the contacts page into `(name, title, phone, email)` records →
   `project_appointment` (Person + `has_role` category `staff`/title, contact attrs) under `eos`.
5. **Prose ingester** — mechanically cleans each prose page to verbatim text → `knowledge_items`
   (normal type, `org_id=eos`), idempotent on a natural key (full-path slug to avoid doc_id collisions
   across hubs — the `0fccfad`-class fix).
6. **Recrawl** — re-walk saved URLs, content-hash diff, reconcile; reuse `explore.py` helpers.

### Data flow

**First crawl (supervised):**
```
seed /parking/ → discover URLs → classify → MANIFEST (dry, no writes)
   → owner reviews/corrects in chat
   → gated live write: ensure_org(eos) + roster→people + prose→KB
   → embed_all → verify_kg + counts (evidence) → owner confirms
```
**Recrawl (unsupervised):**
```
re-walk saved URLs → content-hash diff → re-embed changed → reconcile new/gone → report
```

### Output model (uniform)

- `nodes`: 1 Org (`eos`) + N Person (staff). Edges: `eos --part_of--> njit`; each staff
  `--has_role--> eos` (category `staff`, `attrs.titles=[...]`, contact in Person attrs).
- `knowledge_items`: one per service page, `org_id=eos`, verbatim content, embedded.
- `source='crawler'` (auto-sourced; reconcile/`--reset` scoped to crawler rows, never clobbers manual).

## Error handling / anti-fabrication

- Fetch errors / non-200 → flag in manifest, skip (no partial fabricated content).
- JS-shell empty-clean → `skip:js-shell`, flagged for the owner, never faked.
- `unknown` page type → flagged, never guessed into a type.
- Roster parse: only emit a Person when name + at least one contact field parse cleanly; ambiguous rows
  → flagged for manual confirmation, not invented.
- Verbatim guarantee: prose stored == mechanically-cleaned page text; a test asserts no rewriting.

## Testing (TDD)

- T1. Roster parser → the 5 known EOS staff (name/title/phone/email) from the contacts-page fixture.
- T2. Prose cleaner → verbatim in/out (cleaned text is a substring-preserving transform; no added words).
- T3. Discovery scope → keeps `/parking/*` + service prefixes, drops CSS/JS/PDF/asset, off-scope hosts.
- T4. Classifier → contacts page=`staff-roster`, a service page=`prose-page`, an empty SPA=`skip:js-shell`.
- T5. Change detection → unchanged page = no-op (no re-embed); changed page = re-embed.
- T6. Uniform-output → after ingest, `eos` Org under `njit` + people-with-roles + KB items > 0, matching
  the department-model shape; `verify_kg` passes.

## Open questions / to confirm during the supervised crawl

- Exact staff set (the contacts page lists 5 today; discovery may surface more on sub-pages).
- "Locksmith" + "SchoolDude" — likely a sub-section/SPA respectively; resolve via the manifest.
- Whether the EOS Org's display name should be the long official title or a short "Environmental &
  Operational Services" with the long form as content (NOT an alias — see ND6).

## Goals checklist (to verify at completion)

- [ ] G1 EOS org under njit · [ ] G2 staff as people w/ roles+contact · [ ] G3 service pages verbatim KB,
  embedded, RAG-answerable · [ ] G4 supervised manifest gate · [ ] G5 recrawl diff+reconcile ·
  [ ] G6 uniform output. Each deferral (ND1–ND6) shipped-as-deferred and stated, not silently dropped.
