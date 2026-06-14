# People & Roles Knowledge-Graph Ingestion (YWCC) — Design

**Date:** 2026-06-14
**Status:** Design — **revised per senior review (B1–B3, M1–M4 folded in); ready to plan**
**Author:** Mohammad Dindoost + Claude

## 1. Goal & North Star

Give the **GSA AI assistant** a precise, complete, queryable picture of YWCC people — who
they are, their **role(s)**, their **research**, and how to **contact** them — so RAG can
hand the LLM grounded facts for student-need answers.

**What this work delivers:** the *fetchable, structured facts + retrieval over them* — "who
works on graph in CS," "who are the academic advisors," "how do I reach the marketing
office," each answerable precisely. **What it does NOT deliver (honest scope):** the
*compound orchestration* behind a question like *"I want a PhD on graph — how do I apply
and who would I work with?"* — that combines a (manually-added) admissions-process doc +
people-by-area + a contact, which is the **existing multi-skill planner roadmap**, out of
scope here. This work makes all three facts *individually retrievable* so that planner (and,
today, semantic top-k) can compose them.

**North star:** *"the LLM has precise, complete, well-structured facts to compose a better
answer."* Structured precision (graph) **and** semantic recall (text), not one or the other.

## 2. Why the current design is insufficient

Established during brainstorming and confirmed by a live spike:

- **No college/admin/staff coverage.** Discovery only crawls *department faculty* pages, so
  the Dean's office, directors, staff, and advisors (e.g. Michael Giorgio) can never enter.
- **No multi-role / multi-org model.** `EntityRecord` has a single `org` and an unused
  `role` field. A person who is *Dean + CS Professor* (Payton), *Distinguished Professor +
  Associate Dean* (Wang), or *joint CS↔Informatics* (Borcea, Oria, Nakayama, Phan) gets one
  membership and the rest are silently dropped.
- **Roles aren't captured at all.** The authoritative role source — the listing pages — is
  ignored; org is re-inferred from profile title text.

## 3. Spike findings (what actually works) — 2026-06-14

Tested on `cs.njit.edu/faculty` (58 people) and `people.njit.edu/profile/ikoutis`.

| Task | Deterministic (rules) | Local `llama3.1:8b` |
|---|---|---|
| Enumerate people from a listing | **58/58** | 1/58 |
| Identity slug / name | ✅ | ❌ |
| Roles incl. **dual roles** (Mili, Wang) | ✅ | ❌ |
| Org level (dept vs university) | ✅ Computer Science | ❌ "NJIT" |
| Contact (email/office) | ✅ | partial |
| Structured research-interest list | ✅ split into 4 | ❌ one blob |
| Topics from free **prose** | ❌ (no markup) | ✅ clean, grounded, complementary |

**Conclusion:** deterministic parsing is **authoritative for everything structured —
including research-area *separation*** (the LLM blobbed it); the LLM is a **scalpel used
only to propose *additional* topics from unstructured prose.** The deployed 8B is adequate
for that narrow additive task — no larger model required.

## 4. Architecture — two layers, hybrid extraction

```
                fetch (project UA)          change-detect (hash of normalized structure)
NJIT pages  ───────────────────────►  RAW/TEXT LAYER  ────────────────────────►
(hub + dept                          knowledge_items+FTS+vec (semantic corpus)   only changed
 listings +                          + raw_pages (verbatim snapshot + hash)      pages re-extract
 profiles)                                  │
                                            ▼
                         ┌──────────────── EXTRACTION ────────────────┐
                         │ deterministic (AUTHORITATIVE): people, slug,│
                         │   roles+dual, org membership, contact,      │
                         │   structured research-area list (split)     │
                         │ LLM (scalpel, ADDITIVE prose-only): extra   │
                         │   research topics, grounded + validated     │
                         └─────────────────────┬───────────────────────┘
                                               ▼  (same reconcile txn as the text rows)
                                          GRAPH LAYER
                              (nodes + edges; references organizations;
                               provenance; ontology_version)
                                               │
                  ┌────────────────────────────┴───────────────────────────┐
                  ▼                                                          ▼
        structured retrieval (graph traversal,            semantic retrieval (FTS+vec over
        FTS/plain-SQL only — no vec needed):              the text layer) for narrative /
        faculty_in_org, who-advises, who-works-on         prose / fallback recall
                  └──────────────────────────┬───────────────────────────┘
                                             ▼
                                  RAG context → LLM answer
```

- **Raw/text layer:** (a) existing `knowledge_items` + FTS + vectors (decomposed text,
  reused unchanged) = the **semantic corpus**; (b) a new **`raw_pages`** snapshot store
  (verbatim page + structural hash) for **change-detection and re-extraction**, so the graph
  is re-derivable from stored pages without re-crawling NJIT.
- **Graph layer:** new `nodes` + `edges` tables in the same SQLite DB — the queryable
  structured view, kept consistent with the text layer transactionally (§6.6, B1).

## 5. Data model

### 5.1 Schema (new tables, same SQLite)

```sql
CREATE TABLE raw_pages (                       -- change-detection / re-extraction snapshot
  url            TEXT PRIMARY KEY,
  content        TEXT NOT NULL,                -- verbatim fetched body
  struct_hash    TEXT NOT NULL,               -- hash of the NORMALIZED structure (not raw bytes; see 6.2)
  status         TEXT NOT NULL,               -- 'ok' | 'structureless' | 'error'
  fetched_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE nodes (
  id              INTEGER PRIMARY KEY,
  type            TEXT NOT NULL,              -- Person | Org | ResearchArea  (Document reserved)
  key             TEXT NOT NULL,             -- stable natural key (see 5.3)
  name            TEXT NOT NULL,
  attrs           TEXT NOT NULL DEFAULT '{}',-- JSON: email/phone/office/website/kind; Org carries attrs.org_id (bridge)
  source          TEXT NOT NULL,             -- 'crawler' | 'dashboard' | ...  (scopes reconcile)
  source_doc_id   INTEGER,                   -- the knowledge_items row that created the node (NOT used for sync; see B1)
  ontology_version INTEGER NOT NULL DEFAULT 1,
  is_active       INTEGER NOT NULL DEFAULT 1,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(type, key)
);

CREATE TABLE edges (
  id              INTEGER PRIMARY KEY,
  src_id          INTEGER NOT NULL REFERENCES nodes(id),
  type            TEXT NOT NULL,             -- part_of | has_role | researches  (advises/mentions reserved)
  dst_id          INTEGER NOT NULL REFERENCES nodes(id),
  category        TEXT,                      -- first-class filter for has_role; CHECK below
  area_source     TEXT,                      -- for 'researches': 'structured' | 'prose'
  source_section  TEXT,                      -- for has_role: which listing produced it (gates deactivation, M3)
  attrs           TEXT NOT NULL DEFAULT '{}',-- JSON: titles[] / is_primary / confidence / ...
  source          TEXT NOT NULL,
  source_doc_id   INTEGER,
  ontology_version INTEGER NOT NULL DEFAULT 1,
  is_active       INTEGER NOT NULL DEFAULT 1,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(src_id, type, dst_id),
  CHECK (category IS NULL OR category IN
         ('faculty','staff','admin','advisor','joint','emeritus'))
);
```

- `category` is a **column** (not just an attr) because it's the primary `faculty_in_org`
  filter — a missing key must not silently drop a person (m1).
- One `has_role` edge per (Person, Org); a same-org multi-title case stores
  `attrs.titles = [...]`, `category` = the dominant one (keeps the UNIQUE; m2).
- `area_source` separates the authoritative structured areas from additive prose topics so
  the precise facets can read structured-only (M1).

### 5.2 Ontology (fixed type vocabulary, unlimited instances)

- **Node types (MVP):** `Person`, `Org`, `ResearchArea`. `Document` reserved (future `mentions`).
- **Edge types (MVP):** `part_of` (Org→Org), `has_role` (Person→Org), `researches`
  (Person→ResearchArea). `advises`, `mentions` reserved.
- **Roles are `has_role` edges, not nodes** — one Person, many edges to different Orgs =
  multi/dual-role. `category` ∈ `faculty|staff|admin|advisor|joint|emeritus`.
- **Growable:** new node/edge *types* are deliberate additions to the extractor ontology;
  because the raw layer is kept whole, expanding = re-extract over stored `raw_pages` (no
  re-crawl). `ontology_version` tracks what to rebuild. New *instances* (e.g. a Graduate
  Studies office `Org{kind:"office"}`) need no code change.

### 5.3 Identity & dedup

- Person key = **profile-URL slug** → `Person.key = "p/<slug>"` (existing `entity_id`
  convention; gives free dedup + a join to `knowledge_items.metadata.entity_id`).
- No profile URL → fall back to `name+section` key, `attrs.unverified=true`.
- `Org.key = organizations.slug`; `ResearchArea.key` = canonical form produced by **reusing
  `skills._canonical` / `expand_area` verbatim** (so graph counts match the P2.5 facets, M1).

### 5.4 Org model — reference, don't mirror (B2)

There is **one authoritative org tree: the `organizations` table.** `Org` nodes are a thin
projection: `Org.key = slug`, with `attrs.org_id = organizations.id` as the bridge. `part_of`
edges are **derived from `organizations.parent_id`**, never hand-maintained twice.

```
organizations:  NJIT → YWCC → { College Administration (NEW), Computer Science, Data Science, Informatics }
```
- **College Administration** is created **in `organizations` first** (so `org_id`-scoped
  semantic retrieval and the facets agree), then projected as an `Org` node.
- **Academic advisors** = `category=advisor` under College Administration (not a separate org).
- **NJIT@JerseyCity** = a location label in `attrs`, not an org.

### 5.5 Current DB state (post-wipe, 2026-06-14)

The YWCC/CS/DS/Informatics KB content was removed at the user's request for a fresh build
(backup `.backups/gsa_gateway.20260614-170303.pre-ywcc-wipe.db`). GSA (116) + MMI (42)
remain; the empty YWCC/CS/DS/Informatics org *nodes* are kept. So Phase 1's first crawl is a
**fresh build**, not a supersede; the reconcile machinery below governs *subsequent*
refreshes and protects GSA/MMI.

## 6. Ingestion pipeline

### 6.1 Discovery (hub-first, deterministic)

- Seeds: the YWCC people hub (`computing.njit.edu/people`) sections + the dept listing pages
  in the `DEPARTMENTS` registry.
- Parse with CSS selectors (spike-proven): `<h4>` = section → (org, category); per card
  `a[href*="/profile/"]` = slug, `h1.name` = name, **`p.title` (one or more)** = title(s).
- Output per appearance: `(slug, name, [titles], section, org)` → `Person` + `has_role`
  edge(s), `source_section` recorded on each edge.
- **Discovery records which listings fetched OK** (this gates deactivation; M3).

### 6.2 Raw capture + change detection (M2)

- Store each fetched page in `raw_pages`. The hash is over the **normalized extracted
  structure** (e.g. the `tabbed-content` subtree text / the listing cards), **not raw bytes**
  — so template nonces/CSRF/footer dates don't churn re-extraction.
- Unchanged hash → skip re-extraction; changed → re-extract that page's entities.
- **Shared invariant (M2):** a structureless/failed fetch is recorded `status≠'ok'` and is
  treated as **"no observation," never "absent"** — it must not overwrite text rows *and*
  must not deactivate any node/edge. `njit_adapter.is_valid_profile` is the hard precondition
  for graph extraction too.

### 6.3 Per-entity structured extraction (deterministic, authoritative)

- For each discovered person, fetch the profile and run the existing
  `njit_adapter.parse_entity` (correct for name, titles, org, contact, and the **paren-aware**
  structured research-area split). Populate node `attrs` and `researches` edges with
  `area_source='structured'`. **This is the authoritative separation of research areas.**

### 6.4 LLM prose enrichment (scalpel — ADDITIVE only) (M4)

- The LLM runs **only on the prose** ("About"/bio) and **only proposes *additional* topics**
  — it never re-separates or overrides the structured areas (the spike showed it blobs them).
- A proposed topic is accepted **iff**: (a) case-insensitive token-subset match in the source
  prose **OR** embedding cosine ≥ a pinned threshold against a source sentence; **AND** (b) it
  is not already a `researches` edge for that person (dedup, so counts can't double).
- Accepted topics become `researches` edges with `area_source='prose'`, `attrs.confidence`.

### 6.5 Graph upsert, provenance, versioning

- Upsert nodes/edges by natural key; stamp `ontology_version`; `source_doc_id` = the
  canonical profile row (informational only — sync is by §6.6, not this field; m3).
- Multi-membership = multiple `has_role` edges; `attrs.is_primary` marks the home appointment
  (where research lives; prevents facet double-counting).

### 6.6 Reconcile — graph & text in ONE transaction (B1), section-scoped deactivation (M3)

- **Atomic with the text layer.** Graph upsert/deactivate happens **inside the same
  `reconcile_entity` transaction** that writes the entity's `knowledge_items`, off the *same*
  per-entity diff: the entity's nodes/edges are rebuilt to match its new active text state.
  Text and graph can never diverge (closes the "stale `researches` edge" hole).
- **Section-scoped deactivation (M3).** "Absent now → deactivate" applies to a `has_role`
  edge **only if its `source_section` listing was fetched OK this run.** A failed Informatics
  listing therefore can **not** drop a joint appointee's Informatics edge — protecting
  multi-role completeness. Test required.
- **Manual content untouched.** Deactivation is scoped to `source='crawler'`; `dashboard`/
  GSA/MMI rows are never swept.
- **Giorgio retirement (m5).** Retiring the manual `contact` (id=3317) once the crawler
  covers him is an **explicit, gated, single-row, logged one-off** — NOT part of scoped
  deactivation (it deliberately crosses the manual boundary, so it's called out separately in
  the plan).
- **Gated:** dry-run diff (added/changed/deactivated) + a hardened integrity-checked backup
  (`scripts/_area_tag_migrate.hardened_backup`) before any write.

## 7. Retrieval integration

- **Graph-traversal skills are FTS/plain-SQL only** (edges + `organizations`) → they run on
  the existing **vec-less** structured connection in `message_handler._try_structured`
  unchanged (B3):
  - `faculty_in_org(org)` — `has_role.category='faculty'` joined via `attrs.org_id` →
    `organizations` subtree. **Retires the old `faculty_in_department`** (which counted
    anyone with an `entity_id`); they must not both ship (m6).
  - `people_by_role` / `roles_in_org` — the "C" high-value roles (advisors, deans, directors).
  - `people_by_research_area(area)` — `researches` edges; **unions `area_source` structured
    + prose** (plus the existing FTS-over-statement recall path) so recall is not lost.
- **Precise area facets read structured edges ONLY (M1).** `area_counts` / `areas_in_org` /
  `people_by_area_tag` read `researches` edges with `area_source='structured'`, so
  LLM-inferred topics never inflate "how many faculty list X." Canonicalization reuses
  `skills._canonical`/`expand_area`. **Regression test:** pre/post re-point counts match on a
  saved CS fixture.
- **Semantic** unchanged — FTS + vec over the text layer.
- **Blend / compound queries:** router picks a graph skill when clearly structured, else
  semantic. The compound "apply + who + contact" need is served by semantic top-k today and
  by the **planner** later (explicitly future; §1).

## 8. Failure modes & safeguards

- **Graph↔text divergence:** impossible — upsert/deactivate is in the same reconcile txn (B1).
- **Fetch failure ≠ deletion:** structureless fetch is "no observation," never "absent"
  (M2); deactivation is section-scoped (M3).
- **Research query can't silently lose a person:** structured areas + prose topics + semantic
  — all three must fail for a miss. A bad split is cosmetic. (Retires the comma-fragment class.)
- **LLM hallucination:** additive-only + grounding gate + dedup (M4).
- **Manual content clobbered:** reconcile scoped to `source='crawler'`; Giorgio retirement is
  an explicit logged exception.
- **Every write:** dry-run diff + hardened integrity-checked backup.

## 9. Testing & verification

- **Golden structural tests on SAVED HTML fixtures** (the spike's captured `cs.njit.edu/faculty`
  + a profile), never live fetch (m7): all ~58 CS people; Mili & Wang dual roles; Koutis
  org=Computer Science, 4 split areas, office present.
- **Consistency tests:** after a re-crawl, no `researches` edge is active whose source text
  is inactive (B1); a failed listing never deactivates a joint appointment (M3); a
  structureless fetch deactivates nothing (M2).
- **Facet regression (M1):** pre/post re-point area counts match on the CS fixture; prose
  topics don't change `area_counts`.
- **Reconcile safety:** a re-crawl never touches `source='dashboard'` rows.
- **Extraction:** grounding gate rejects an unsupported topic and de-dups against structured.
- **Operational:** dry-run diff reviewed; coverage metrics each run; golden-eval after refresh.

## 10. Phased rollout (each phase = one implementation plan)

The two hardest problems (B1 graph↔text sync, B2 org bridge) land **first, on existing data**,
before any new coverage:

1. **Phase 1a — consistency foundation.** `raw_pages` + `nodes`/`edges` schema; the
   `organizations`-bridge (`Org.key=slug`, `attrs.org_id`, `part_of` from `parent_id`); graph
   upsert/deactivate **wired into `reconcile_entity` (B1)**; structured extraction (reuse
   adapter) → `researches` (structured) + `has_role` edges. Proven by re-ingesting a **saved
   CS fixture** (data was wiped) and asserting graph↔text consistency. No new coverage.
2. **Phase 1b — coverage.** Hub-first discovery; **create College Administration in
   `organizations`**; staff/advisors; dual-role + joint appointments; section-scoped
   deactivation (M3). Crawl CS/DS/Informatics + College Admin fresh.
3. **Phase 2 — LLM prose enrichment.** Additive-only topic extraction + grounding/dedup gate
   (M4); `researches` prose edges.
4. **Phase 3 — retrieval on the graph.** Graph skills + category filtering; retire old
   `faculty_in_department` (m6); re-point precise area facets to structured edges (M1) with the
   regression test; router wiring.

## 11. Out of scope (future)

- Crawlers for non-YWCC colleges / other institutions (manual or their own).
- `Document`/`mentions`, `advises`; `Course`/`Scholarship` node types.
- The multi-skill **planner** for compound queries (the PhD-application answer).
- `ontology_version` re-extraction *runner* — named as the mechanism; a job to re-extract
  stored `raw_pages` on an ontology bump is **deferred** (m4) and not counted as a current
  safeguard until built.
- A larger extraction model (8B confirmed adequate for the additive prose task).

## 12. Key decisions (recap)

- **Deterministic is authoritative** (incl. area separation); LLM is **additive prose-only** (M4).
- **One org tree** (`organizations`); `Org` nodes reference it via `attrs.org_id` (B2).
- **Graph & text reconcile in one transaction**; deactivation is **section-scoped** (B1, M3).
- This work delivers *fetchable facts + retrieval*; compound orchestration is the **planner**
  (future) — no over-promise (B3).
- Fixed-but-growable ontology; raw layer kept whole so the graph is a re-derivable view.
- GSA/MMI and all manual content are never touched by the crawler.
