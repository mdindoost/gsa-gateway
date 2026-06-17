# International Content Pilot (Categories D + L: I-20 / SEVIS / CPT / OPT) — Design

**Date:** 2026-06-17
**Status:** Approved (design); pending senior-eng review before build
**Relates to:** `project_day_to_day_intents` (second content pilot — categories D + L of the 150
intents), `2026-06-17-office-routing-pilot-design.md` (reuses that pipeline), the
immigration **heads-up** (already live: immigration questions get "confirm with OGI" appended).

## Goal

Answer the international-student day-to-day intents (D: I-20 / SEVIS / arrival; L: CPT / OPT /
on-campus work) with **safe, useful overviews that always route to OGI** — never asserting
volatile immigration specifics (exact fees, form numbers, edge cases) the GSA bot isn't the
authority on. Coverage is net-new (only the OGI office contact + the F-1 full-time doc touch
this today).

**Content depth (user-chosen):** overview + always-route-to-OGI. Each topic = what it is →
basic eligibility → general process & key timing → "file/confirm through OGI" + the OGI page
link. The immigration heads-up auto-appends the disclaimer. We do **not** author exact fees /
form numbers / dollar amounts that go stale or mislead.

## Principle: reuse what we built (no bespoke paths)

- **Sourcing:** the maintainer brings the OGI page content (paste + source URL, like the office
  pages) — fastest and most accurate; I draft from it, he verifies in one pass. (I may
  WebFetch to fill gaps.)
- **KB ingest:** the existing gated doc pipeline — section-aware chunker, per-section
  `entity_id` + shared `doc_id`, `source='dashboard'`, `doc_type='policy'`, then `embed_all.py`;
  `hardened_backup` + dry-run + `--commit`.
- **KG:** all six docs file under the **existing OGI org** (slug `ogi`) — no new orgs needed.
- **Answering:** unchanged — RAG + cross-encoder rerank surfaces the right OGI overview; the
  immigration heads-up fires.
- **Gate:** a fast deterministic chunk-level test (no LLM generation), like the office-routing gate.

## Components

### 1. Content (~6 OGI overview docs, drafted from OGI pages, verified by the maintainer)

In `bot/data/sources/international/<slug>.md`, each a single-topic doc with front-matter
`title` + `source_url` and a body of the form *what it is → eligibility → process & timing →
"file/confirm through OGI" + link*:

| slug | covers (D + L intents) |
|---|---|
| `cpt` | CPT: what it is, eligibility, timing, location/remote rules — apply through OGI |
| `opt-stem-opt` | OPT (pre/post-completion, apply ~90 days before graduation) + STEM OPT extension |
| `i-20-and-arrival` | requesting the I-20, financial documents, processing time, visa delay / late arrival / **deferral**, mandatory international orientation |
| `sevis` | SEVIS record, the SEVIS fee, transferring SEVIS to NJIT |
| `on-campus-employment` | F-1 on-campus work eligibility + hours |
| `maintaining-f1-status` | staying in status, reporting changes, who to contact (links the existing F-1 full-time doc, does **not** duplicate it) |

Multi-section docs are fine (section chunker handles them). The final/each doc points to OGI;
the heads-up reinforces it.

### 2. Ingest, KG, answering

- Drop the 6 docs in `bot/data/sources/international/` and add **one folder→org mapping** to
  `ingest_office_docs.py`: `"international": ("ogi", "Office of Global Initiatives", "njit",
  "office")` — its folder→single-org pattern fits since all 6 are under OGI. `doc_type` for
  these is **`policy`** (procedures), so pass it (ingest_office_docs currently hardcodes
  `doc_type='policy'` — already correct).
- `ensure_org('ogi')` is a no-op (OGI exists); `sync_org_nodes` keeps the graph consistent.
- Answering = the rerank stack + the immigration heads-up (both already shipped).

### 3. Entity capture (standing principle)

Any new OGI sub-office or named OGI adviser the maintainer provides → captured (org via
`ensure_org`, person via `people_editor`, verified, gated) — same as the office pilot.

## Testing & acceptance gate

`v2/tests/test_international_gold.py` (deterministic, chunk-level, fast — no LLM):
- A frozen `{question → gold token}` map = the D + L intents **+ author-independent paraphrases**
  (~30–40 queries). Tokens are id-stable (e.g. "Curricular Practical Training" / "Optional
  Practical Training" / "SEVIS" / "I-20"), present in the right doc.
- Assert the gold topic chunk is in the reranked **top-2** for every query.
- **Overlap guard:** "OPT *job search*" must still → Career Development (office pilot), while
  "how do I apply for OPT" → the OGI `opt-stem-opt` doc. Include both so the new content doesn't
  cannibalize the office routing.
- **0 regressions:** guard set of existing answers (travel award, officers, office routing).
- A heads-up assertion: a CPT/OPT/visa question matches the immigration heads-up (already true).

**Acceptance bar:** every D + L intent + paraphrase → correct OGI topic at rank ≤2; the
OPT-job-vs-OPT-apply overlap holds both ways; 0 regressions; maintainer-verified content.

## Out of scope
- Exact fees / form numbers / step-by-step filing (deliberately — OGI is the authority).
- The other categories (A/B/C/E admissions, F/G funding/billing, H/I/K registration, J/N/O
  academic) — separate pilots.
- A full OGI crawler (future Spec B).

## Files
- Create `bot/data/sources/international/<slug>.md` (6 docs).
- Modify `scripts/ingest_office_docs.py` — add the `"international"` folder→OGI mapping.
- Create `v2/tests/test_international_gold.py` + `v2/tests/international_gold.py`.
