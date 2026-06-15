# GSA Website → KG + KB (Crawl-Based) Design

**Date:** 2026-06-15
**Status:** Design — pending implementation plan
**Author:** brainstormed with Mohammad Dindoost (VP Academic Affairs)

## Goal

Replace GSA's hand-written Q&A knowledge with the same two-layer model used for YWCC:
a **graph layer** (people, roles, orgs) and a **text layer** (prose from primary
documents), sourced by **crawling gsanjit.com as an entry point** and kept current by
**re-crawl + reconciliation**. Officer turnover (e.g. Mohith Oduru leaving the E-Board)
is handled automatically by the existing departure machinery — no manual upkeep.

## Why (problem)

- GSA content is hand-authored Q&A (~46 `faq` items) that Claude originally summarized
  *from* GSA PDFs — a lossy copy-of-a-copy. Rebuilding from the QA would compound that
  loss; we should rebuild from the **primary documents**.
- The rigid `Q:/A:` format hurts retrieval (interrogative phrasing pollution — see the
  `there`/`are there` stopword bug fixed 2026-06-15).
- GSA is structurally identical to what we already model for YWCC: an org with people in
  roles, sub-orgs (clubs/RGOs) with their own officers, plus policy/procedure prose. It
  should use the **same ontology and pipeline**, not a parallel representation.

## Decisions locked in (from brainstorming)

1. **Scope: GSA only.** MMI stays in QA for a later, separate spec (it needs a new
   Event/Talk ontology we are deliberately deferring).
2. **Crawl-based, not dashboard-authored.** gsanjit.com is the entry point; re-crawl +
   reconcile updates everything. (This reverses an earlier "dashboard-authored" decision
   and the older "the crawler is YWCC-only / GSA is manual" principle — GSA people and
   documents are now crawled; only ad-hoc corrections happen in the dashboard.)
3. **All-conversational bot.** Only `/qrcode` remains a slash command. `/contact`,
   `/help`, and the (already-removed) `/ask /events /resources /initiative /feedback`
   are answered conversationally via retrieval. `bot_features.md` is replaced by a
   truthful capability doc. (Conversational *actions* — submitting an initiative or
   feedback through chat — are intent→action routing, a SEPARATE future piece, not built
   here.)
4. **Reuse `explore()` + `reconcile` + M3 departures wholesale**; add a GSA-Wix
   extraction adapter and GSA entry points.
5. **KB from primary PDFs/pages**, not the QA. The QA is retired but reused as a
   *coverage checklist*.
6. **Gated workflow preserved**: dry-run + hardened backup + mandatory alignment check
   before any live write; project UA on all fetches; never send personal data outbound.

## Architecture

```
                 explore()  +  reconcile  +  M3 departures        (generic, exists)
                     │
        ┌────────────┴────────────┐
   NJIT adapter              GSA-Wix adapter            ← NEW (the hard part)
 (parse_listing,           (extract E-Board / DepRep /
  server HTML)              RGO rosters + doc links
                            from gsanjit.com Wix)
```

The crawl engine (`v2/core/ingestion/explore.py`), the graph projection
(`project_appointment`, `sync_org_nodes`, org tree), the text reconcile, change-detection
(`struct_hash`), and section-scoped deactivation (M3) are **unchanged**. GSA support is
two additions: **(a)** GSA entry points, **(b)** a GSA extraction adapter that turns Wix
pages into the same `EntityRecord`/listing structures the NJIT adapter produces.

This is the "pluggable extraction adapter" generalization previously noted as a
multi-institution follow-up; GSA is its first additional consumer.

## Data model (KG)

Reuses existing node/edge types. **No new node or edge types.** Two new role
**categories**: `officer`, `deprep`.

```
NJIT
└── GSA  (Org)
    ├── PhD Club / <RGO>   (Org, part_of GSA)         ← from the RGO page
    └── …
People (source='crawler', from gsanjit.com):
  Person  has_role(category=officer, title="VP Finances")  → GSA
  Person  has_role(category=officer, title="President")    → <RGO>
  Person  has_role(category=deprep,  title="Dept Rep")     → GSA
          (+ optional affiliated_with → their department, if the page states it)
```

- **Person key**: `gsanjit.com/<page>/<slug>` (mirrors the NJIT `people.njit.edu/...`
  key convention) so re-crawl resolves the same node and reconcile can deactivate it.
- **Org key**: slug (`gsa`, `phd-club`, …), `part_of` edges to GSA / NJIT.
- The manually-seeded President (Teik C. Lim, `source='dashboard'`) and any dashboard
  corrections are preserved across re-crawls by the existing source-scoped reset
  (`DELETE … WHERE source='crawler'`).

## Extraction adapter (Wix) — feasibility spike FIRST

gsanjit.com is Wix/React (~1.39 MB, framework soup; rosters are not in clean HTML). The
adapter's method is **unknown until we inspect the Wix internals**, so the first
implementation task is a **read-only spike**:

- **Inspect** the E-Board / DepRep / RGO pages for: embedded JSON state
  (`window.__INITIAL_STATE__` / Wix `warmupData` / a Wix CMS "Collection" data blob), or
  a queryable Wix data endpoint.
- **Decision criteria:**
  - **Deterministic parse (preferred):** if rosters live in embedded JSON / a CMS
    collection → parse that directly. Reliable, no LLM, fits the no-hallucination bar.
  - **Rendered fallback:** if data is only in client-rendered DOM → use a rendering fetch
    (headless) or constrained LLM-extraction with the guardrails proven in the
    affiliation probe (self-grounding: every extracted name/role must appear verbatim in
    the page; reject empty/ungrounded; human-flag low confidence). LLM-extraction is the
    last resort because of the recall/temporal issues we measured.
- The spike's output is a one-page finding that selects the method; the rest of the build
  is written against that choice.

## Entry points & crawl scope

GSA entry points (added to `entry_points.py` or a GSA-specific entry list):

- Home / "The People" hub → E-Board, DepRep, RGO listing pages.
- Governance → E-Board, DepRep, General Assembly.
- RGO → current RGOs (each → an Org + its officers).
- Documents/Forms page → links to the source PDFs.

Bounded BFS, same as YWCC (hub → listing → profile/section). Depth and scope mirror the
existing runner flags.

## Document (PDF) ingestion → KB

- Download the linked GSA documents (Constitution & Bylaws, Club Finance Bylaws, Travel
  Award info, funding/forms) with the **project UA**.
- Store raw (like `raw_pages`), extract text, chunk into `knowledge_items`
  (`created_by='crawler'` or a `gsa-doc` source), set `source_url` to the document URL
  for provenance, and embed via `embed_all.py` (resumable).
- Page prose (about/governance/program descriptions) is chunked into KB the same way.
- **Publications/webpage exclusion** (from the 2026-06-15 retrieval fix) does not apply
  to these GSA doc types, so they are retrievable in normal answers.

## Updates & turnover (reconcile + M3)

Re-crawl is the update mechanism. Turnover is the existing **section-scoped
deactivation**:

- Mohith is on the E-Board page today → `Person(mohith) has_role(officer, VP Finances) →
  GSA` active.
- Next crawl after he leaves: he is no longer in the E-Board listing → his GSA `has_role`
  edge is deactivated (M3), and KB items derived from him are retired; the new VP Finance
  gets a fresh edge. Identical to a departed-faculty sweep.
- Re-crawl is idempotent (change-detection via `struct_hash`); unchanged pages are
  skipped.

## Bot read-path (all conversational)

- New structured skill **`officers_in_org(conn, org_id)`** querying `has_role`
  (category `officer`/`deprep`) → org, plus router patterns: "who is the GSA president /
  VP finance", "who are the GSA officers", "who represents <dept>", "who is president of
  <RGO>". Descriptive questions still fall through to RAG (conservative router).
- Procedures / policies / programs answered by RAG over the new clean KB prose.
- `contact_boost` stays (still helps person queries) but is no longer load-bearing.
- `/qrcode` unchanged; `/contact` + `/help` removed; `bot_features.md` → truthful
  capability doc describing what the bot can do conversationally (interface-agnostic).

## Migration of existing content

- A one-time step retires the ~46 GSA `faq` items (`is_active=0`, kept for history) and
  uses them as a **coverage checklist** to confirm the crawled KB answers every common
  question.
- `contacts.yml` stops being a runtime source (`/contact` is gone). It may be kept as a
  fallback seed only.

## Gated workflow & safety

- Every live write preceded by `hardened_backup(...)`; dry-run by default; `--commit`
  required.
- Mandatory **alignment check** (extend `verify_kg`): every officer/DepRep/RGO present on
  the site is in the KG; no orphan nodes; no active leftover QA items; KB filed under the
  right org.
- Project UA on all fetches; **never** send personal email/data outbound.

## Testing

1. **Adapter (unit):** given a saved Wix page fixture, the adapter returns the expected
   roster (names, roles, org); empty/garbled input yields no spurious people (no
   fabrication).
2. **KG seed:** expected officer/deprep nodes + `has_role` edges (count, category, title);
   RGOs `part_of` GSA; idempotent re-crawl (second run: 0 new, correct skips).
3. **Turnover:** simulate a person dropped from a listing → their edge deactivates, a new
   person's edge activates (M3), KB items retired.
4. **Golden retrieval:** a question set ("who is VP finance", "how do I apply for a travel
   award", "what is 3MRP", "how do I become a DepRep", "who is president of <RGO>")
   returns the right KG/KB answer; old QA-phrasing pollution gone.
5. **Alignment check** passes (no orphans / mis-files / leftover QA).
6. **Router:** new officer patterns route correctly; descriptive questions fall through.

## Out of scope / deferred

- **MMI** migration (needs Event/Talk ontology) — separate spec.
- **Events/programs as structured nodes** (3MRP, socials) — stay KB prose for now.
- **Phase-2 LLM affiliation extraction** — deferred (text layer suffices; see
  2026-06-15 affiliation probe).
- **Conversational actions** (submit initiative/feedback via chat) — intent→action
  routing, separate work.
- **WorldCup** command interface — unrelated real-time feature.
- **Full adapter-framework generalization** beyond what GSA needs — extract the seam, but
  don't over-build for hypothetical third institutions.

## Open risks

- **Wix extraction feasibility** is the central unknown — resolved by the task-1 spike;
  if no deterministic path exists, the rendered/LLM fallback raises effort and fragility.
- **Site structure churn** — Wix layouts change; the adapter must fail safe (a
  failed/empty fetch must never deactivate existing people, same guard as the NJIT
  listing sweep).
- **PDF format variance** — scanned vs text PDFs; the doc-ingestion step needs a text
  check and a fallback.
