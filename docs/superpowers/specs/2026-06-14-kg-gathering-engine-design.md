# Knowledge-Graph Gathering Engine — Design

**Date:** 2026-06-14
**Status:** Design — consolidated from a hands-on walkthrough of the live YWCC pages
**Author:** Mohammad Dindoost + Claude
**Builds on:** `2026-06-14-people-roles-kg-ingestion-design.md` (two-layer model, blockers B1/B2)
and the already-built **Phase 1a** (`docs/superpowers/plans/2026-06-14-phase1a-graph-foundation.md`):
`nodes`/`edges`/`raw_pages` schema, graph CRUD, the `organizations` bridge, and graph
projection wired into `reconcile_entity` in one transaction.

## 1. Philosophy (the one rule)

**Gather everything. Never throw anything away. Structure the important relations as graph
edges; keep all the rest as raw text. RAG then uses both — the precise graph and the full
raw — and the raw layer is the catch-all that guarantees no loss.**

- We are **gatherers, not arbiters of truth.** If a source is messy (a glued research area)
  or two sources conflict (Mili vs Wu as "Associate Dean for Academic Affairs"), we capture
  it faithfully **with provenance** and do not "fix" NJIT's data.
- **Important + recurring relation → promote to an edge type** (and re-extract over saved raw
  to backfill). **Not important → the raw text already carries it**, retrievable semantically.
- **North star:** the LLM gets precise, complete facts to answer student needs. This work
  delivers the *fetchable facts + retrieval*; the compound-question *orchestration* (e.g.
  "PhD on graph — how to apply + who to work with") is the existing planner roadmap, not here.

## 2. What the live walkthrough proved (2026-06-14)

Walked `computing.njit.edu/people` → College Administration → CS faculty → 6 profiles →
personal sites. Findings that shaped this design:

- **`/people` is a hub:** it lists the **5 children** (College Administration, CS, DS,
  Informatics, Academic Advisors), each with a "Learn More" link — not people directly.
- **College Administration** = 13 people (Dean / Associate Deans / Staff), each a clean
  `(slug, name, title)` — **the section is the role signal**, not the title.
- **CS faculty** = 58 across 6 rank sections; **Payton and Wang appear in *both* College
  Admin and CS** → one node, multiple appointments across paths (multi-membership).
- **Richness gradient:** professors are rich (areas, ~40–100 publications, bio, a *real
  personal website*); lecturers are thin (contact + role only). Coverage must be relative.
- **Personal sites enrich beyond NJIT pages:** Koutis → "Associate Chair of Graduate
  Studies", awards, software; Wang → "Founding Director, AI Center", a **joint appointment in
  the Martin Tuchman School of Management** (outside YWCC), IEEE Fellow. They also expose a
  **deeper frontier** (publications.html, people.html→students) and **redirect**
  (`web.njit.edu/~ikoutis` → `ikoutis.github.io`).
- **Deterministic wins on structure** (58/58 listings, profile fields); **LLM is only for
  prose** (personal-site narratives) — confirmed by the earlier spike (8B got 1/58 on a list).

## 3. Core concepts

### 3.1 Entry point = link + prior knowledge + aspect

An entry point is a **seed URL that already knows what it is**:
`entry_point = (url, known_node, node_type, aspect)`. Example:
`("https://computing.njit.edu/people", YWCC, "college", "people")` — we know from the KB that
YWCC is a college under NJIT; we don't re-derive it.

- **Prior knowledge anchors identity and flows DOWN.** A person found under the CS section
  inherits **`org = CS` and nothing else** — their **role is *found*** from the section/title,
  never assumed (so we never blanket-stamp "faculty"). This is also what fixed the spike's
  "said NJIT instead of CS" failure: we *tell* the extractor the org.
- **Aspect = which facet we crawl.** `/people` is the *people* aspect of YWCC; a future
  `/news` would be the *news* aspect of the same org. Same node, different entry points.
- Entry points exist at **any level** (college, dept, person, personal page), so a run can
  start anywhere and re-runs can target one branch.

### 3.2 Frontier = a node's "next steps"

Anything discovered-but-not-yet-explored (a child link, a personal website, a `people.html`
of students) is recorded as a **pending next-step on the node** via a `has_source`/frontier
entry. The graph **always knows there is more here**, even when a run stops. We never have to
explore it now — we log it for later.

### 3.3 `explore(start_node, depth, aspect)` — bounded BFS

A run is **breadth-first from a start node, bounded by `depth`** (hops to follow). From the
YWCC `/people` entry point:

| depth | reaches |
|---|---|
| 0 | the `/people` hub itself |
| 1 | College Admin, CS, DS, Informatics, Advisors pages |
| 2 | the people (profiles) listed on those |
| 3 | each person's personal website |
| 4 | personal-site sub-pages (publications.html, **people.html → students**) |

So a `depth 2` run never reaches students — they are **logged as frontier** for a later run.
You can also launch a focused dive: `explore(Koutis_personal_site, depth=10, aspect=people)`.
**Depth bounds this run's effort; the frontier persists what's left — "there is always more"
is by design.**

### 3.4 Two completeness signals

- **Per-node extraction coverage** — how much of a node's raw became graph. `<100%` = raw
  not yet structured (still answerable semantically); `100%` = fully structured; **`>100%`,
  measured against the UNION of a node's sources, = a relation no source supports = a
  hallucination alarm.** (Measured against one source, cross-path enrichment is normal.)
  Relative to what a node *has*: a lecturer at "100%" is just role+contact.
- **Exploration frontier/depth** — how much of the reachable graph we have walked.

### 3.5 Change-detection (recrawl is a separate pass)

Re-running `explore(start, depth)` re-fetches just that bounded subtree, compares each page's
`struct_hash` (hash of the **normalized structure**, not raw bytes — M2) to the stored
`raw_pages`, and **only re-extracts what changed** since the last visit.

## 4. Edge ontology (core now, growable)

Each row is **evidence-backed** from the walkthrough. Start with `core`; promote a `growable`
row to a real edge type (and re-extract over saved raw) when it's important and recurring;
otherwise the raw layer carries it.

| Relation | Meaning | Evidence | Status |
|---|---|---|---|
| `part_of` | org → parent org | NJIT→YWCC→CS/DS/Informatics | **core** |
| `has_role` | person → org (with `category`) | Dean, Professor, Director, Lecturer | **core** |
| `researches` | person → research area | structured areas + prose topics | **core** |
| `has_source` | node → raw page (frontier) | profiles, personal sites, sub-pages | **core** |
| `authored` | person → publication | Oria's 38 papers | growable |
| `advises` | person → person (student) | Wang's people.html | growable |
| `affiliated_with` | person → non-home org | Wang @ Tuchman / AI Center | growable |
| `honored_with` | person → award | Koutis best-paper; Wang IEEE Fellow | growable |

**Node types:** `Person`, `Org`, `ResearchArea` (core); `Publication`, `Award`, `Document`
(growable). `category` ∈ `faculty|staff|admin|advisor|joint|emeritus` (from the **section**).

## 5. Data model additions (beyond Phase 1a)

Phase 1a already has `nodes`, `edges` (with `category`, `area_source`, `source_section`,
`source`, `is_active`, `ontology_version`), and `raw_pages(url, content, struct_hash, status)`.
This engine adds:

- **`frontier`** — pending next-steps:
  `(id, from_node_id, url, aspect, status['pending'|'fetched'|'error'], depth_discovered,
  discovered_at)`. Implemented as rows (or `has_source` edges carrying `attrs.status`).
- **raw↔node is many-to-many** — a `page_nodes(raw_url, node_id)` link (a listing page informs
  many people; a profile/personal site, one). Drives coverage and provenance.
- **coverage (per node)** — derived, not stored authoritatively: `consumed_spans /
  (total_raw_spans + pending_frontier)`. Stored as a cached `nodes.attrs.coverage` for
  dashboards, recomputed on extraction.
- **out-of-scope orgs** — when a personal site names a non-YWCC unit (AI Center, Tuchman
  School), create a **stub `Org` node** with `attrs.prior_knowledge="NJIT unit"`,
  `attrs.scope="out_of_tree"`, and an `affiliated_with` edge — recorded, flagged, not crawled
  (no entry point for it unless one is added later).

## 6. The `explore()` engine

```
explore(start_node, depth, aspect):
  queue = [(start_node.url, start_node.context, depth)]
  while queue (BFS, level by level):
    url, ctx, d = queue.pop_front()
    final_url, html, status = fetch(url)         # follow redirects; project UA; robots
    h = save_raw_page(final_url, html, status)    # raw_pages upsert
    if status != 'ok':                            # structureless/error = "no observation"
        mark frontier item 'error'; continue      # never deactivates anything (M2)
    if h == stored_hash(final_url):               # unchanged since last visit
        skip extraction                           # change-detection
    else:
        record = extract(final_url, html, ctx)    # deterministic structure + (Phase 2) LLM prose
        with reconcile_txn:                       # B1: graph + text atomic
            upsert nodes/edges (project_entity / org / listing rows)
            link page_nodes(final_url, node_ids)
            recompute coverage for touched nodes
    for child in discovered_links(html, ctx):     # children inherit ctx (org, prior knowledge)
        add frontier(from_node, child.url, aspect)
        if d > 0: queue.push_back((child.url, child.ctx, d-1))
        # else: recorded as frontier only — explored in a future, deeper run
```

- **Discovery is deterministic** (CSS selectors: `<h4>` section, `a[href*="/profile/"]`,
  `h1.name`, `p.title`) for listings; **role category comes from the section**.
- **Profiles** use `njit_adapter.parse_entity` (already correct: name/titles/org/contact/
  paren-aware areas) → `project_entity` (Phase 1a).
- **Personal sites** (unstructured) → Phase 2 LLM-on-prose (additive, validated); until then
  saved as raw (semantic) and logged as frontier.
- **`is_valid_profile` is the precondition for graph extraction** — a structureless fetch is
  "no observation," never "absent" (never deactivates nodes/edges).

## 7. Extraction strategy (carried + refined)

- **Deterministic is authoritative** for all structure, including **research-area separation**
  (the LLM blobbed it in the spike). **Section → category** (Dean/Associate Deans/Staff/
  Professors/Lecturers/Emeriti), not title-keyword guessing.
- **LLM is additive, prose-only** (Phase 2): proposes *extra* topics/roles/affiliations from
  bios and personal sites; accepted only if grounded (token-subset OR embedding-sim against a
  source sentence) AND not already an edge (dedup). Never overwrites structured facts.
- **Multi-membership:** one Person (keyed by profile slug / `entity_id`), many `has_role`
  edges across orgs/paths; `attrs.is_primary` marks the home appointment.

## 8. Retrieval use (gather → KG → RAG)

- **Structured graph traversal** (FTS/plain-SQL only, runs on the existing vec-less structured
  connection): `faculty_in_org` (`has_role.category='faculty'` via the org bridge),
  `people_by_role`/`roles_in_org`, `people_by_research_area` (union of structured + prose
  `researches` edges).
- **Semantic** over the raw/text layer (FTS + vectors) — this is where the **unprocessed
  remainder** (publications, personal-site prose, un-promoted relations) stays answerable.
- The router picks a graph skill when clearly structured, else semantic; **RAG composes from
  both**. Precise area facets read `area_source='structured'` only (no LLM inflation).

## 9. Failure modes & safeguards

- **Faithful capture, not arbitration:** conflicts/messiness stored with provenance, never
  "corrected."
- **Never discard:** anything unstructured stays in raw and is semantically retrievable.
- **Graph↔text never diverge:** projection is in the reconcile transaction (B1, built).
- **Fetch failure ≠ deletion:** structureless = "no observation" (M2); deactivation is
  section-scoped (a failed listing can't drop a joint appointment — M3).
- **Manual content untouched:** reconcile scoped to `source='crawler'`; GSA/MMI and
  hand-added rows are never swept (Giorgio's manual contact retired as a logged one-off once
  the crawler covers `mg833`).
- **Every write:** dry-run diff + hardened integrity-checked backup.

## 10. Build order

1. **Phase 1a — graph foundation + reconcile consistency. ✅ BUILT** (branch `feat/kg-phase1a`).
2. **Phase 1b — the `explore()` engine:** entry-point registry (url+prior-knowledge+aspect);
   BFS with `depth`; `frontier` table + `page_nodes` link; redirect-following fetch; hub +
   listing discovery; **section→category**; profile extraction via the adapter→`project_entity`;
   change-detection (struct_hash skip). Runs YWCC `/people` → College Admin + CS/DS/Informatics
   people. Multi-membership + dual roles across paths.
3. **Phase 1c — coverage metric:** span/provenance tracking; per-node coverage (incl. the
   `>100%` hallucination alarm against the source union); dashboard surfacing.
4. **Phase 2 — LLM prose enrichment:** additive topics/roles/affiliations from bios &
   personal sites (grounded + dedup gate); promote `affiliated_with`/`advises` as needed.
5. **Phase 3 — retrieval on the graph:** graph-traversal skills + category filtering; retire
   old `faculty_in_department`; re-point P2.5 area facets to `researches` (structured) edges.

## 11. Out of scope (future)

- Crawlers for non-YWCC colleges / other institutions (manual, or their own).
- `Document`/`mentions`, the compound-question **planner**.
- The `ontology_version` **re-extraction runner** (re-extract stored `raw_pages` on an
  ontology bump) — named mechanism, deferred until a growable type is actually promoted.

## 12. Key decisions (recap)

- **Gather, never discard;** important relations → edges, the rest → raw (catch-all for RAG).
- **Entry point = link + prior knowledge + aspect;** org inherited down, role found from section.
- **Bounded BFS `explore(start, depth, aspect)`;** frontier persists "there is always more."
- **Two completeness signals:** per-node coverage (with `>100%` = hallucination alarm) +
  exploration frontier.
- **Deterministic authoritative, LLM additive prose-only;** faithful capture, provenance,
  graph↔text atomic, manual content untouched.
