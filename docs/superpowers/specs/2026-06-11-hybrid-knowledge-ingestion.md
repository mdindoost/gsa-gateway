# Hybrid Knowledge Ingestion & Retrieval Pipeline — Design Spec

**Status:** design (pre-implementation) · **Date:** 2026-06-11 · **Author:** Mohammad + assistant

## 0. Goal & guiding principles

Turn arbitrary web sources (NJIT faculty profiles today; personal sites, Google
Scholar/DBLP, other departments/universities tomorrow) into a knowledge base that
lets the bot answer **substantive** questions accurately — e.g. *"which CS faculty
work on graph algorithms?"* — with **citations**.

Principles (per the "no caps, specialist" directive):

1. **Decompose, never truncate.** The reason the old crawler capped (`research[:800]`,
   `publications[:6]`, `chunk[:1500]`) was that a giant card both *bloats the LLM
   context* and *dilutes the retrieval embedding*. The correct fix is not a cap —
   it is to **split each entity into focused, self-contained searchable units**
   (small-to-big / parent-document retrieval). Then size is never the problem.
2. **Flexibility by separation of concerns.** Fetching, extracting, structuring,
   verifying, indexing, and retrieving are independent stages. New source shapes
   slot in at the fetch/extract layer without touching the rest.
3. **Precise where structure is stable; LLM where it isn't.** Don't write a parser
   per site. Use precise adapters only for uniform templates (NJIT `people.njit.edu`);
   use fetch→LLM-extract everywhere else.
4. **Provenance on every fact.** Every stored unit carries its source URL,
   extraction method, verification status, confidence, and crawl time. Verified
   facts are authoritative; LLM-sourced/unverified content is labeled and used for
   recall, never stated as hard fact.
5. **Grounded over generated.** Prefer summarizing/​extracting from real fetched
   sources over an LLM's memory; when memory is used (RARR-style enrichment), it is
   verified against sources before it counts as fact.
6. **Optimize the end state: LLM answer quality.** Every decision is judged by
   whether it helps the answering LLM retrieve the *right focused evidence* and
   ground a correct, cited answer.

Grounded in established techniques: RAG (Lewis 2020), HyDE (Gao 2022, LLM-generated
text for retrieval), RARR (Google 2023, generate→research→revise→attribute),
document expansion (Doc2Query), small-to-big / parent-document retrieval.

---

## 1. Architecture overview

```
SOURCE ROUTER ── per entity, one or more sources
   │
   ▼
FETCH LAYER ───────────  URL → clean text
   ├─ static HTML (readability/boilerplate strip)
   ├─ PDF (CV, paper lists) → text
   └─ JS/SPA → headless render (Playwright) when needed
   │   politeness: project UA, rate-limit, robots.txt, cache
   ▼
EXTRACT LAYER ─────────  text → structured ENTITY RECORD
   ├─ PRECISE ADAPTER   (known template, e.g. NJIT people.njit.edu)  [cheap, exact]
   └─ GENERIC LLM-EXTRACT (any shape, grounded in fetched text)      [flexible]
   │
   ▼
ENRICH / VERIFY (optional, staged) ──  breadth + trust
   ├─ multi-source MERGE (NJIT page + personal site + Scholar/DBLP)
   ├─ RARR enrichment: powerful-LLM draft → web-search verify → attribute
   └─ each claim → {verified(+source url) | refuted(drop) | low-confidence(review)}
   │
   ▼
DECOMPOSE ─────────────  ENTITY RECORD → many focused, context-carrying ITEMS
   profile · research_statement · publication(×N) · award(×N) · teaching · service
   │  (each item self-contained: carries entity name + provenance)
   ▼
INDEX ─────────────────  per item: embedding (sqlite-vec) + FTS(search_text)
   versioned (root_id/parent_id/is_active), provenance in metadata
   ▼
RETRIEVE (small-to-big) ─ hybrid (semantic+keyword) over ALL items
   match focused unit → EXPAND to parent profile for context
   ▼
GENERATE ──────────────  focused, attributed context → answer w/ doc_id + source citations
```

---

## 2. Stage detail

### 2.1 Source router
A registry mapping an **entity** (a faculty member, an office, a club) to one or
more **sources**, each with an adapter:

| source kind | adapter | notes |
|---|---|---|
| `njit_profile` | precise selector parser | uniform across all NJIT depts; just swap the listing URL per dept |
| `personal_site` | generic fetch + LLM-extract | arbitrary shape |
| `scholar` / `dblp` | semi-structured adapter | authoritative, complete publication list |
| `web_search` | RARR verifier | for claims not on any crawled page |

Adding a department = a new listing URL (reuse the NJIT adapter). Adding a personal
site / new university / new source kind = the generic path, **no new selectors**.

### 2.2 Fetch layer
- One generic fetcher: `fetch(url) -> {text, content_type, final_url, fetched_at}`.
- HTML → main-content extraction (strip nav/boilerplate). PDF → text. JS-heavy →
  Playwright headless render (only when a static fetch yields too little).
- Politeness: self-identifying UA (project URL, **no personal data** —
  [[feedback_outbound_personal_data]]), per-host rate limiting, robots.txt respect,
  on-disk cache (so re-runs and verification don't re-hit hosts).

### 2.3 Extract layer
Output of either path is one normalized **EntityRecord** (no caps):
```
EntityRecord {
  name, aliases[], titles[], role, department/org,
  research_statement (full text),
  research_areas[] (topical tags/keywords — drives "who works on X"),
  publications[] { title, venue, year, coauthors[], url? },   # ALL of them
  awards[], teaching[], service[], education[], links{website,scholar,...},
  contact { email?, office?, phone? },
  provenance[] { field, source_url, method, fetched_at }
}
```
- **Precise adapter:** CSS selectors → fields (NJIT template). Cheap, exact, no LLM.
- **Generic LLM-extract:** prompt = the fetched text + a strict JSON schema; the LLM
  fills only what the text supports ("extract, do not invent; leave blank if absent").
  Local llama for grounded extraction; configurable to a stronger model.
- The two paths produce the *same* EntityRecord shape, so downstream is uniform.

### 2.4 Enrich / verify (staged — see §5)
- **Merge** multiple sources into one EntityRecord (e.g. NJIT title + Scholar's full
  publication list + website's bio), tracking per-field provenance.
- **RARR enrichment (optional):** a strong LLM drafts breadth from broad knowledge →
  each claim is **web-searched and checked against trust-ranked sources** (`.edu`,
  DBLP, Scholar, official conference/Wikipedia) → verified claims keep their source
  URL; refuted dropped; low-confidence → review list. (See [[feedback_v2_gated_workflow]].)

### 2.5 Decompose (the "no cap" core)
One EntityRecord → **many** `knowledge_items`, each focused and **self-contained**:

| item `type` | content | why a separate item |
|---|---|---|
| `profile` | name, titles, role, dept, contact, 1-line research summary | the "who is this" anchor; the parent |
| `research_statement` | full research description (uncapped) | semantic anchor for research queries |
| `publication` (×N) | `"<Name> — <title> (<venue> <year>)"` + abstract if available | so *"graph algorithms paper"* matches the **specific** paper |
| `award` / `teaching` / `service` (×N or grouped) | one fact each, name-prefixed | precise recall, no dilution |

Each non-profile item sets `parent_id`/`root_id` → its `profile`. Each item is
small and topical, so **there is nothing to cap** — and the retrieval embedding for
a publication is about *that paper*, not drowned by 100 others.

### 2.6 Index
- Reuse `knowledge_items` (already has `org_id, type, title, content, metadata,
  source_url, version, root_id, parent_id, is_active`).
- Per item: sqlite-vec embedding + FTS5 over `search_text`.
- `metadata`: `{ source_url, method: selector|llm, verified: true|false|review,
  confidence, fetched_at, entity_id }`.
- Versioned updates: re-crawl → new version row, old `is_active=0` (existing scheme).

### 2.7 Retrieve (small-to-big)
- Hybrid RRF (semantic + keyword) over **all** item types (already built).
- **Expand-to-parent:** when a `publication`/`research_statement`/`award` item is
  retrieved, also pull its `profile` parent (one cheap join), so the answering LLM
  gets *the matched evidence* **and** *who it belongs to*.
- Return focused items + their parents — no giant card, so **no 1500-char cap
  needed**; the generation budget becomes a tunable, generous number, not a crutch.

### 2.8 Generate
- Context = the focused matched items + parent profiles, each labeled with its
  `doc_id` (already built) and `source_url`.
- The LLM answers grounded, cites `doc_id` + source; verified vs. unverified status
  is visible so it never asserts an unverified fact as official.
- Pairs with the **retrieval debug trace** (already built) for full observability.

---

## 3. Worked example — "which CS faculty work on graph algorithms?"

1. Query embeds + FTS over all items.
2. Matches: Koutis's `research_statement` ("spectral graph theory, Laplacian
   solvers…") and several `publication` items ("…— A nearly-m log n solver for SDD
   systems…"), each name-prefixed → `parent_id` = Koutis `profile`.
3. Expand-to-parent pulls the `profile` (name, title, contact).
4. LLM answers: *"Ioannis Koutis (doc_id 178) — spectral graph theory & Laplacian
   solvers [source: people.njit.edu/…]; see 'A nearly-m log n solver…' (doc_id 412)."*

Today this fails because the fact is either uncaptured (website / paper #7) or
diluted in one capped card. The decomposed design makes it a direct hit.

---

## 4. Data & trust model
- **Authoritative layer:** verified facts → what the bot states.
- **Recall layer:** all extracted text (incl. unverified-but-plausible) → embedded
  for *finding*, never asserted as fact. Stating ≠ finding.
- Every item carries provenance; low-confidence/unverified routed to a human review
  list before going live (gated workflow).

---

## 5. Phasing (ship value early, add ambition deliberately)
- **Phase 1 — grounded baseline (no external API, no hallucination):**
  richer NJIT crawl (Service + ALL publications + website text via generic
  fetch+LLM-extract using local llama) → DECOMPOSE → index. Likely already answers
  most "who works on X". *This is where we start.*
- **Phase 2 — multi-source breadth:** add Scholar/DBLP adapters → complete,
  verified publication lists.
- **Phase 3 — RARR enrichment + web verification:** strong-LLM draft → web-verify →
  attributed facts for breadth not on any page (awards, news).

Each phase is independently shippable and reviewable.

---

## 6. Open decisions (to confirm before building)
1. **Extraction model for the generic path:** local llama (free/private) vs. an API
   model (stronger). Start local.
2. **Search provider for Phase-3 verification:** Serper.dev (cheap) / Google Custom
   Search (100/day free) / Bing. Needs a key.
3. **Headless rendering:** add Playwright now (handles JS sites) or defer until a
   source needs it.
4. **Embedding model:** keep `nomic-embed-text`, or move to a stronger one given the
   richer, decomposed corpus.
5. **Review threshold:** confidence bar above which a claim auto-publishes vs. goes
   to the review list.

---

## 7b. Revisions from senior design review (2026-06-11)

The review surfaced issues that materially change Phase 1. Binding decisions:

- **R1 (was C1) — structural link goes in `metadata.entity_id`, NOT `parent_id`/`root_id`.**
  `parent_id`/`root_id` are already owned by the **versioning** scheme (prev-version /
  version-group, with a `set_root` trigger and the upsert). They must keep that
  meaning. The "this publication belongs to that profile" link lives in
  `metadata.entity_id` (a stable per-entity key, e.g. the profile `source_url` or a
  slug). "Expand-to-parent" = "fetch the active `profile` item with the same
  `metadata.entity_id`" (a `json_extract` lookup), not a `parent_id` join.

- **R2 (was C2) — item-level reconcile, not a single upsert.** An entity now becomes
  many items that change independently but share one `source_url`. Each item gets a
  **stable identity** = `entity_id` + `type` + natural key (e.g. publication title
  hash). On re-crawl: diff new items vs. the entity's active items → INSERT new,
  version-bump changed, deactivate disappeared. The current `WHERE source_url=?`
  upsert can't do this and is replaced by an entity-scoped reconcile step.

- **R3 (was I3/I4) — retrieval-time per-entity grouping (a *retrieval* bound, not a
  content cap).** After RRF fusion, group candidates by `metadata.entity_id`, take
  top-k children per entity (k≈1–2), then the global top-N *distinct entities*; then
  expand each to its `profile` + its best matched children. This stops one person's
  N publications from crowding out other people on broad queries. `limit`/`pool_size`
  become tunable (and larger for the bigger corpus) — a principled bound, measured.

- **R4 (was I5) — carry provenance to generation.** Extend `RetrievedChunk`/`V1Chunk`
  to include `source_url` and `verified`, and have the prompt builder render them, so
  the LLM can cite sources and never assert an unverified fact as official. (The
  `doc_id` half already works; `source_url`/`verified` are new plumbing.)

- **R5 (was I6) — typed-context prefix per item.** Each item's `content` starts with a
  typed context line, e.g. `"Publication by Ioannis Koutis (NJIT Computer Science): <title>"`,
  so even abstract-less items carry entity + venue into the embedding. Always emit a
  `research_statement` and `research_areas` item when present — those carry the topical
  signal that wins "who works on X".

- **R6 (was M9) — defer generic website LLM-extract to Phase 1b.** It is the riskiest,
  slowest piece and is not needed to prove the thesis. Phase 1a uses only the
  LLM-free precise NJIT adapter. When the generic path lands, constrain output
  (`format: json` + schema validation), drop invalid/ungrounded extractions to the
  review list, and substring-check each field against the fetched text.

- **R7 (principled bounds are allowed; arbitrary truncation is not.** Per-item natural
  size (one paper, one section), retrieval-time grouping, embed-only-missing, and
  flagging a 500-publication / 50-page-CV outlier to the review list are *principled
  bounds*, not the forbidden content caps.)

### Re-scoped Phase 1a — smallest valuable first increment (build this)
LLM-free, deterministic, proves the whole path end-to-end:
1. **Decompose** the precise NJIT adapter's output into items: `profile`,
   `research_statement`, `publication`×N, `award`/`teaching`×N — each name-prefixed
   (R5), `metadata.entity_id` (R1), `verified=true`, `source_url`.
2. **Item-level reconcile/versioning** (R2).
3. **Retriever: per-entity grouping + expand-to-parent** (R3), tunable `limit`.
4. **Carry `source_url` + `verified` to the prompt** (R4).
This already answers "which CS faculty work on graph algorithms?" from
`research_statement` + `publication` items. Generic website LLM-extract = Phase 1b.

## 7. Non-goals
- A universal "understand any website perfectly" guarantee (impossible); we aim for
  robust best-effort + provenance + review for the uncertain tail.
- Replacing the precise NJIT adapter (kept — strictly better where structure holds).
