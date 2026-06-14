# People & Roles Knowledge-Graph Ingestion (YWCC) — Design

**Date:** 2026-06-14
**Status:** Design (approved in brainstorming; pending spec review)
**Author:** Mohammad Dindoost + Claude

## 1. Goal & North Star

Give the **GSA AI assistant** a precise, complete, queryable picture of YWCC people — who
they are, their **role(s)**, their **research**, and how to **contact** them — so the LLM
can ground real student-need answers, e.g. *"I want to do a PhD in YWCC working on graph /
LLMs — how do I apply and who would I work with?"*

**North star:** every design choice serves *"the LLM has precise, complete, well-structured
facts to compose a better answer."* Structured precision (graph) **and** semantic recall
(text), not one or the other.

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

**Conclusion:** deterministic parsing is primary for everything structured; the LLM is a
**scalpel used only on unstructured prose.** The deployed 8B is adequate for that narrow
task — no larger model required.

## 4. Architecture — two layers, hybrid extraction

```
                fetch (project UA)            change-detect (content hash)
NJIT pages  ───────────────────────►  RAW LAYER  ──────────────────────────►
(hub + dept                          (knowledge_items + FTS + vectors;        only changed
 listings +                           every page kept verbatim, lossless)     pages re-extract
 profiles)                                  │
                                            ▼
                         ┌──────────────── EXTRACTION ────────────────┐
                         │ deterministic (primary): people, slug,      │
                         │   roles+dual, org membership, contact,      │
                         │   structured research-interest list         │
                         │ LLM (scalpel, prose only): extra research   │
                         │   topics from bios, grounded + validated    │
                         └─────────────────────┬───────────────────────┘
                                               ▼
                                          GRAPH LAYER
                              (nodes + edges, typed, provenance,
                               versioned; the queryable structured view)
                                               │
                  ┌────────────────────────────┴───────────────────────────┐
                  ▼                                                          ▼
        structured retrieval (graph traversal)                semantic retrieval (FTS+vec
        "faculty in CS", "who advises", "who                  over the raw layer) for prose
         works on graph", role lookups                        / narrative / fallback recall
                  └──────────────────────────┬───────────────────────────┘
                                             ▼
                                  RAG context → LLM answer
```

- **Raw/text layer** has two parts: (a) the existing `knowledge_items` + FTS + vectors —
  decomposed, self-contained text rows, reused unchanged — which are the **lossless text
  corpus for semantic recall**; and (b) a lightweight **page-snapshot store** (`raw_pages`:
  verbatim page + content hash) added for **change detection and re-extraction**, so the
  graph can be re-derived from stored pages without re-crawling NJIT.
- **Graph layer** = new `nodes` + `edges` tables in the same SQLite DB (no new database).
  The structured, queryable view, re-derivable from the raw/text layer at any time.

## 5. Data model

### 5.1 Graph schema (new tables, same SQLite)

```sql
CREATE TABLE nodes (
  id              INTEGER PRIMARY KEY,
  type            TEXT NOT NULL,            -- Person | Org | ResearchArea  (Document reserved)
  key             TEXT NOT NULL,            -- stable natural key (see 5.3)
  name            TEXT NOT NULL,            -- display name
  attrs           TEXT NOT NULL DEFAULT '{}', -- JSON: email/phone/office/website/kind/...
  source          TEXT NOT NULL,            -- 'crawler' | 'dashboard' | ...  (scopes reconcile)
  source_doc_id   INTEGER,                  -- provenance → knowledge_items.id
  ontology_version INTEGER NOT NULL DEFAULT 1,
  is_active       INTEGER NOT NULL DEFAULT 1,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(type, key)
);

CREATE TABLE edges (
  id              INTEGER PRIMARY KEY,
  src_id          INTEGER NOT NULL REFERENCES nodes(id),
  type            TEXT NOT NULL,            -- part_of | has_role | researches  (advises/mentions reserved)
  dst_id          INTEGER NOT NULL REFERENCES nodes(id),
  attrs           TEXT NOT NULL DEFAULT '{}', -- JSON: title/category/is_primary/source/confidence/...
  source          TEXT NOT NULL,
  source_doc_id   INTEGER,
  ontology_version INTEGER NOT NULL DEFAULT 1,
  is_active       INTEGER NOT NULL DEFAULT 1,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(src_id, type, dst_id)
);
```

### 5.2 Ontology (fixed type vocabulary, unlimited instances)

- **Node types (MVP):** `Person`, `Org`, `ResearchArea`. `Document` reserved for a future
  `mentions` capability.
- **Edge types (MVP):** `part_of` (Org→Org), `has_role` (Person→Org), `researches`
  (Person→ResearchArea). `advises`, `mentions` reserved.
- **Roles are edges, not nodes.** A "role/appointment" is a `has_role` edge from Person→Org
  carrying `attrs = { title, category, is_primary, source_section }`. A job title is not a
  shared entity, and this makes *"faculty in CS"* a one-hop edge filter and multi-role
  natural (one Person, many `has_role` edges to different Orgs).
- **`category`** ∈ `faculty | staff | admin | advisor | joint | emeritus`, normalized by
  rules from the listing section + title.
- **Growable:** new node/edge *types* are deliberate one-line additions to the extractor's
  ontology; because the raw layer is kept whole, expanding the ontology = re-extract over
  stored raw (no re-crawl). `ontology_version` tracks what to rebuild. New *instances*
  (e.g. a "Graduate Studies Office" `Org{kind:"office"}`) need no code change.

### 5.3 Identity & dedup

- A person's stable key is their **profile-URL slug** → `Person.key = "p/<slug>"` (matches
  the existing `entity_id` convention). This de-dupes someone listed in several sections
  into one `Person` node with multiple `has_role` edges.
- People with no profile URL fall back to a `name+section` key, `attrs.unverified=true`.
- `Org.key` = slug; `ResearchArea.key` = casefolded canonical form (reuses the area
  canonicalization from `skills.py`).

### 5.4 Org tree

```
NJIT
└─ YWCC
   ├─ College Administration   (NEW Org{kind:"admin-unit"}) — Dean, Assoc Deans, Directors, staff, advisors
   ├─ Computer Science
   ├─ Data Science
   └─ Informatics
```
- **Academic advisors** = `category=advisor` under College Administration (not a separate Org).
- **NJIT@JerseyCity** = a location label in `attrs`, not an Org node.
- Org nodes mirror the `organizations` table; `part_of` edges encode the hierarchy.

## 6. Ingestion pipeline

### 6.1 Discovery (hub-first, deterministic)

- Seeds: the YWCC people hub (`computing.njit.edu/people`) sections — College Administration,
  CS, DS, Informatics, Academic Advisors — plus the department faculty/listing pages already
  in the `DEPARTMENTS` registry.
- Parse each listing with CSS selectors (proven in the spike): `<h4>` = section → rank/
  category; per card `a[href*="/profile/"]` = slug, `h1.name` = name, `p.title` (one or
  **more**) = title(s)/dual roles.
- Output per appearance: `(slug, name, [titles], section, org)` → upserted as `Person` +
  `has_role` edge(s). Section→(org, category) via a small rules table.

### 6.2 Raw capture + change detection

- Store each fetched page verbatim in the **`raw_pages`** snapshot store with a **content
  hash**. On re-crawl, an unchanged hash skips re-extraction; a changed hash flags the page
  (and its entities) for re-extraction. (The decomposed *text* for semantic search still
  lives in `knowledge_items`; `raw_pages` is only the snapshot for change-detection and
  re-extraction.) The existing **skip-not-clobber guard** still applies: a structureless or
  failed fetch never overwrites a good record.

### 6.3 Per-entity structured extraction (deterministic, reuse)

- For each discovered person, fetch their profile and run the existing
  `njit_adapter.parse_entity` (already correct for name, titles, org, contact, and the
  **paren-aware** structured research-interest list). Populate node `attrs` (email, phone,
  office, website) and `researches` edges (`attrs.source="structured"`).

### 6.4 LLM prose enrichment (scalpel, validated)

- For the **prose** fields only (the "About"/bio narrative), call the local 8B with a tight,
  grounded prompt → research topics as JSON. Add as `researches` edges with
  `attrs.source="prose"`, `confidence`. **Research-area separation is LLM-judgment-first,
  paren-aware-regex fallback** (the model treats `ML (a, b, c)` as one area).
- **Validation gate:** accept an extracted topic only if it is supported by the source text
  (substring/embedding-similarity check); drop anything not grounded. The LLM never
  overwrites structured facts — it only *adds*.

### 6.5 Graph upsert, provenance, versioning

- Upsert nodes/edges by natural key; set `source_doc_id` to the raw row each came from;
  stamp `ontology_version`. Multi-membership = multiple `has_role` edges on one Person.
- `is_primary` on the home-department appointment (where research lives; prevents
  double-counting in facets).

### 6.6 Reconcile / in-place migration (no wipe)

- **In-place supersede.** Re-ingest over existing data; reconcile version-supersedes per
  entity (existing behavior).
- **Scoped deactivation:** "present-before / absent-now → deactivate" runs **only over
  `source='crawler'` YWCC nodes/edges.** Manual content (`source='dashboard'`, e.g. GSA
  roster, the hand-added Giorgio contact, the future PhD-process doc) is never touched.
- **Gated:** dry-run + diff (added / changed / deactivated) and a **hardened, integrity-
  checked backup** (`scripts/_area_tag_migrate.hardened_backup`) before any write.
- Once the crawler covers YWCC admin, the manual Giorgio `contact` (id=3317) becomes
  redundant and is retired in the migration.

## 7. Retrieval integration

- **Structured (graph traversal) skills:** `faculty_in_org(org)` (now filters
  `has_role.category=faculty`, fixing the staff-counted-as-faculty break), `people_by_role`
  / `roles_in_org` (the "C" high-value roles: advisors, deans, directors),
  `people_by_research_area(area)` (via `researches` edges + the area canonicalization).
- **Semantic:** unchanged — FTS + vectors over the raw layer for narrative / prose / recall.
- **Blend:** the deterministic router picks a graph skill when the question is clearly
  structured, else semantic RAG. Compound needs ("apply for PhD + graph + who to contact")
  are served by semantic top-k today; deeper multi-skill orchestration is the existing
  planner roadmap (out of scope here).
- The research-area facets shipped in P2.5 are re-pointed to read the graph
  (`researches` edges) rather than `metadata.areas`; behavior preserved, source unified.

## 8. Failure modes & safeguards

- **Research query can't silently lose a person:** three independent paths — structured
  areas, prose topics, semantic over raw — must *all* fail for a miss. A bad split is
  cosmetic, not a silent miss. (Directly retires the "comma-split fragmentation" class.)
- **LLM hallucination:** grounding/validation gate; LLM only adds, never overwrites.
- **Manual content clobbered:** reconcile scoped to `source='crawler'`.
- **Bad/empty fetch:** skip-not-clobber guard.
- **Every write:** dry-run diff + hardened integrity-checked backup.

## 9. Testing & verification

- **Golden structural checks:** CS listing yields all ~58 people; Mili & Wang carry dual
  roles; Koutis resolves org=Computer Science with 4 split areas + office; staff (Giorgio)
  excluded from `faculty_in_org`.
- **Extraction validation tests:** prose-topic grounding gate rejects an unsupported topic;
  paren-internal commas never fragment an area.
- **Reconcile safety tests:** a re-crawl never deactivates `source='dashboard'` rows.
- **Operational:** dry-run diff reviewed before commit; coverage metrics (people, edges,
  areas) reported each run; golden-eval regression after refresh (existing roadmap item).

## 10. Phased rollout (each phase = one implementation plan)

1. **Graph foundation + deterministic discovery:** `nodes`/`edges` schema; hub+listing
   parse; structured per-entity extraction (reuse adapter); graph upsert; reconcile (scoped,
   gated, backup). Migrate CS/DS in place; add College Administration + staff.
2. **LLM prose enrichment:** grounded topic extraction + validation gate; `researches`
   prose edges.
3. **Retrieval on the graph:** graph-traversal skills + role/category filtering; re-point
   the P2.5 area facets to `researches` edges; router wiring.

## 11. Out of scope (future)

- Crawlers for non-YWCC colleges / other institutions (they add manually or write their own).
- `Document`/`mentions` and `advises` edges; `Course`/`Scholarship` node types.
- Multi-skill planner for compound queries.
- A larger extraction model (8B confirmed adequate for prose).

## 12. Key decisions (recap)

- In-place re-ingest, **no wipe** (skip-not-clobber + dry-run + backup make it safer/more
  complete than a reset).
- **Deterministic-primary, LLM-prose-only** extraction (spike-validated).
- Fixed-but-growable ontology; **roles as `has_role` edges**; raw layer kept whole so the
  graph is a re-derivable view.
- GSA and other manual content stay manual and are never touched by the crawler.
