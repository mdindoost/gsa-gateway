# YWCC College/Department Crawler — Design

**Date:** 2026-06-25
**Status:** Draft for review (RAG + senior-eng review → owner sign-off → build TDD)
**Author:** Claude (brainstormed with Mohammad)
**Project:** First "colleges & departments" member of Crawling 2.1 (see `project_crawling_2_1`).
Pilot for the eventual full DB wipe + rebuild.

---

## 1. Goals

1. Apply the office-style **"DFS the entry point and grab WHATEVER is there"** behavior to an academic
   unit (YWCC), so we capture program / advising / **student** / **news** / **event** prose — not just
   people. Today colleges/depts are crawled **only** for people (narrow `explore.py` hub→listing→profile),
   so all that prose is missing.
2. Keep the rich **people + research-area** layer intact and unchanged (`explore.py` already does it well).
3. Make **entry points granular and independently recrawlable**: the college AND each department is its
   own entry point; an admin can recrawl all of YWCC or just one department.
4. Type **news** and **events** separately from policy, and capture their **dates**, so retrieval can
   rank by recency / upcoming-vs-past.
5. Honor every standing hard line: crawl = mechanical/data-bringing only (no usage decisions in the
   crawler), NJIT content served verbatim/never withheld, gated live writes, source-tag discipline.

### Non-goals (explicitly deferred)
- The dedicated **"what's new / latest news" digest intent** (retrieval feature) — deferred to the rebuild.
- The **publications-intent route** (making the 15.7k paper-title items reachable) — deferred to the rebuild.
- **M2 chunking** of long pages — separate corpus-wide project; this design only ensures compatibility.
- **ND6 departure reconciliation** for prose (a page removed from the site is not auto-retired) —
  deferred-with-flag, consistent with the office crawlers.
- Other colleges (NCE, CSLA, HCAD, MTSM) — YWCC is the pilot; the pattern generalizes after.

---

## 2. Background — current YWCC state (evidence, live DB 2026-06-25)

YWCC org subtree: `ywcc` (id 4, college) → `computer-science` (5), `data-science` (6), `informatics` (7),
`college-administration` (10, unit).

Active `knowledge_items` under the YWCC subtree:

| type | created_by | count | notes |
|---|---|---|---|
| publication | crawler | 3,078 | one paper title each, from `people.njit.edu/profile/<slug>` via `decompose.py` |
| profile / teaching / education / award / research_* / about / service / experience | crawler | ~620 | people-side, from `explore.py` |
| research_areas | scholar | 66 | Scholar enrichment |
| webpage | crawler | 51 | raw personal-site dumps (frontier pass) |
| **policy** | **dashboard** | **27** | **MANUAL — must NOT be clobbered (`source='dashboard'`)** |

Prose source hosts (non-publication): `people.njit.edu` (623, people-side), plus a thin scattering of real
prose — `cs.njit.edu` (~12), `informatics.njit.edu` (~7), `catalog.njit.edu` (~10), personal sites.

**Takeaways:**
- The college/dept **prose is essentially un-crawled today** — this crawler is **net-new and mostly additive**.
- Departments are on **their own subdomains** (`cs.njit.edu`, `informatics.njit.edu`, …), not paths under
  `computing.njit.edu`. → entry points must be **host-anchored** (disjoint subtrees, clean per-entry recrawl).
- `explore.py` ROOT is already `computing.njit.edu/people` and resolves YWCC depts via the hub
  (`entry_points._CHILDREN`). The people layer needs **no change**.

---

## 3. Architecture — two engines, one runner

```
                         YWCC runner (the orchestration unit)
                        /                                      \
   PEOPLE engine = explore.py (UNCHANGED)            PROSE engine = NEW ywcc prose crawler
   people.njit.edu profiles → KG + per-person KB     computing/cs/informatics.njit.edu subtrees
   (people/roles/research, M3 reconcile)             → knowledge_items (policy/news/event)
   created_by='crawler'                              created_by='ywcc_crawl'
```

Two well-bounded engines, one runner = the single "entry-point" operation. Rationale for NOT unifying:
`explore.py` produces decomposed, entity-tagged records (`metadata.entity_id`, `type='profile'`) that the
retriever expands via `_diversify_and_expand`; the prose crawler produces page-level records with no
`entity_id`. Merging would force one record shape onto the other and pollute both retrieval paths. (RAG
review, 2026-06-25.)

### 3.1 Entry-point model (the granularity refinement)

Entry points are **first-class, host-anchored, and independently recrawlable**. For YWCC:

| entry point | host (seed) | org | independently recrawlable |
|---|---|---|---|
| YWCC (college) | `https://computing.njit.edu/` | `ywcc` | ✅ |
| Computer Science | `https://cs.njit.edu/` | `computer-science` | ✅ |
| Informatics | `https://informatics.njit.edu/` | `informatics` | ✅ |
| Data Science | `https://datascience.njit.edu/` *(confirm at dry-run)* | `data-science` | ✅ |

- An admin picks one entry point → recrawl **only that subtree** (dashboard "Data Sources" job, same as
  offices). College and dept entry points are **disjoint subdomains**, so a single-entry recrawl never
  touches another unit. `college-administration` prose, if any, rides under the `computing.njit.edu`
  college entry point.
- The exact host list (esp. Data Science) and per-entry page counts are confirmed by a **dry-run** at
  build time (Bursar lesson: recon via the real crawler dry-run, not WebFetch).
- **Prose entry points live in their OWN registry, NOT `ALL_ENTRY_POINTS`** (corrected per senior-eng
  review): `ALL_ENTRY_POINTS` is consumed by `explore.py` as people hubs/listings — a prose seed added there
  would make the people crawler try to crawl it as a roster (and perturb `discoverable_host`). The prose
  engine gets a separate `PROSE_ENTRY_POINTS` list (or an `aspect='prose'` tag `explore.py` skips). "Add a
  dept/college = add a prose entry point" still holds — just in the prose registry.

---

## 4. The prose engine (new) — `ywcc_crawl.py`

Modeled on `eos_crawl.py` (reuse the proven DFS spine: fetch → mechanical `clean_text` → content-hash
idempotency → version-bump). **Changes vs the office template:**

### 4.1 Scoping — REUSE the existing spine as-is (corrected per senior-eng review)
The crawl spine is **already host+path scoped**; there is **no cross-subdomain bug and no new host-scoping
code**. `crawl_entry` → `select_links(…, relevance_gated=False)` → `same_scope` = `same_site` (host equality,
`web_crawler.py:103`) AND under `scope_prefix`. A `cs.njit.edu` seed **already cannot** yield a
`computing.njit.edu` / `people.njit.edu` URL — cross-host links are dropped at the source.
The one requirement: **entry-point seeds must be bare-host roots** (`https://cs.njit.edu/`). For a bare host
`scope_prefix` returns `/` → the whole subdomain (exactly what we want), and the office `_in_scope`(seed_path
=`/`) then passes everything on that host. (The office crawler needed its extra `_in_scope` only because
office seeds are *paths* on the shared `www.njit.edu` host, where `scope_prefix` widens to `/`; subdomain
seeds don't have that problem.) Add a test that a subdomain seed never yields an off-host URL — as a guard on
existing behavior, not new code. The genuinely-new work is §4.2/§4.3/§4.4/§4.5.

### 4.2 People/prose URL partition (the real net-new guard)
The cross-host `people.njit.edu` skip is **already free** (`same_scope`, §4.1). The genuinely-new guard is
skipping **in-host people pages** so a roster doesn't compete with the structured KG answers. Single source
of truth = **`entry_points.SUPPLEMENTARY_PATHS`** (`entry_points.py:115`: `/people`, `/faculty`, `/staff`,
`/our-people`, `/administration`, `/leadership`, `/joint-faculty`, …) — the exact in-host paths `explore.py`
treats as people listings. (YWCC depts are resolved dynamically via the hub `_CHILDREN`, so there are no
per-dept listing EntryPoints to derive from — `SUPPLEMENTARY_PATHS` is the correct signal.)
**Match semantics (for TDD):** skip when the URL path **equals** a SUPPLEMENTARY segment or is **under** it
(`/faculty`, `/faculty/…`) — a *segment* match, NOT substring, so `/faculty-handbook` (real prose) is kept
and `/our-faculty` is matched. A program page that merely *mentions* a name inline is kept; we skip only
dedicated roster pages.

### 4.3 Mechanical page typing (NEW)
Each captured page is stamped a `type` by **URL path** (mechanical, not meaning):
- path contains `/news` or `/announcement` → `type='news'`
- path contains `/event` → `type='event'`
- else → `type='policy'`

The crawler **only labels**; it makes **no** visibility/exclusion decision (those live in the retriever —
§6). Mistyping (a news landing under `/about`) degrades safely to `policy` (still served); we do **not** add
title/text heuristics to "improve" typing (that would be editing-for-meaning).

### 4.4 Date capture (NEW — STRUCTURED dates only)
Mechanically extract literal dates into `metadata`, on first crawl, **only from structured markup** (so it
stays mechanical, never free-text date-parsing, which borders on meaning-extraction):
- `metadata.published_at` — `<time datetime>`, `<meta property="article:published_time">`, JSON-LD
  `datePublished` (ISO-8601).
- `metadata.event_start` / `metadata.event_end` — JSON-LD `Event` `startDate`/`endDate`, or a `<time>` element.
- `metadata.source_updated_at` — Drupal "changed" / `dateModified` when present.

**Free-text date-range parsing is explicitly DEFERRED** (not deterministically testable; borderline vs the
mechanical-only hard line). A page with no structured date simply has no date field.
Rationale: news/event pages get deleted/rewritten by NJIT and we are heading for a DB wipe — a date not
captured on first sight is **unrecoverable**.

### 4.5 Source tagging (the reconcile-safety fix)
Prose rows are written `created_by='ywcc_crawl'`, `source='crawler'`. **Isolation is two-fold:** (1) prose
rows have **no `metadata.entity_id`**, and `reconcile_entity` matches `created_by` AND `entity_id`
(`reconcile.py:100-101`) — so prose is invisible to it even within `'crawler'`; (2) the distinct `created_by`
is belt-and-suspenders. The **load-bearing** reason for the distinct tag is the **idempotency upsert**
(`eos_crawl.py:329-332`: `WHERE org_id=? AND natural_key=? AND created_by=?`) — the prose crawler **shares
org_ids** with `explore.py` people rows (orgs 5/6/7), so the `created_by` filter is what stops that SELECT
matching a people row. `explore.py`'s `reconcile_departures` (`created_by='crawler'`) also can't see
`ywcc_crawl`; vectors are item-id-keyed → no collision. `source='crawler'` keeps the dashboard `--reset` /
"crawler-owned" semantics over both producers. (Offices shared `'crawler'` safely only because they don't
share an org subtree with `explore.py`; YWCC does.)

### 4.6 Figures / assets
Unchanged from `eos_crawl.py`: capture `img src+alt` and linked pdf/jpg/png as structured `metadata.images`
/ `metadata.files` (literal page data, never described); strip only site-wide near-universal chrome assets.

### 4.7 Idempotency, politeness & recrawl
Per-page content-hash, exactly as `eos_crawl.py`: unchanged page → skip; changed → version-bump
(`is_active=0`). A single-entry-point recrawl re-walks only that subdomain.
- **Politeness (gap in the office spine — must add):** `crawl_entry` has **no inter-fetch delay** (unlike
  `crawl_site`'s `delay`). College subdomains are large (potentially hundreds of pages × up to 4 entries) —
  add a per-fetch delay / throttled fetcher to avoid rate-limiting/blocks.
- **Per-entry budget:** `eos_crawl` defaults `budget=300, max_depth=4` PER entry; set explicit per-entry
  budgets for the larger college sites and **log/report the `truncated` flag** when a budget is hit.
- **natural_key index:** the idempotency SELECT filters `json_extract(metadata,'$.natural_key')`; add an
  index (offices got away without one at ~30 rows; YWCC prose is larger × repeated recrawls).
- **ND6** (a page removed from the site is not auto-retired) is **deferred-with-flag**, consistent with offices.

---

## 5. The people engine — `explore.py` (UNCHANGED)

No changes. It already anchors YWCC at `computing.njit.edu/people` and resolves CS / Data Science /
Informatics / College-Administration via the hub (`_CHILDREN`). It owns people, roles, research areas,
home-appointment-only, M3 departure reconcile, personal-site frontier, and the 3,078 publications
(`decompose.py`). The YWCC runner simply invokes it as the people pass.

---

## 6. Retrieval-layer changes (SEPARATE piece — `retriever.py`)

Per the crawl-vs-usage hard line, the crawler only stamps types + captures dates; **all ranking/visibility
lives here.** This is a distinct, separately-reviewed slice of work.

| change | behavior |
|---|---|
| **News** (`type='news'`) | **Served** in the default corpus, with a **type prior < 1.0 × recency decay with a FLOOR** (exponential half-life on `published_at`, ~180d; floor ≈ 0.5 so old news is demoted, never erased). Demote = routing; erase = withholding. |
| **Events** (`type='event'`) | Boost **only when `event_end >= now`** (upcoming); past events stay in-corpus, unboosted, recency-decayed. Pass the row (not just the type) to `_boost_for`. |
| **`event_info`** | Unchanged — remains GSA-curated only (its unconditional 1.2× boost must NOT apply to crawled/expired events). |
| **`webpage`** (55) | Remove from exclude list → **serve at a `<1.0` prior in `_boost_for`** (e.g. 0.8) — a *real* downweight, not parity (`_boost_for` returns 1.0 for everything non-`event_info` today, so "downweight" needs an actual prior). Low volume; reachable, not hidden. |
| **`office_page`** | **Drop from `DEFAULT_EXCLUDE_TYPES`** (dead type, 0 rows). |
| **`publication`** (15.7k) | **Stays excluded** from the general flood (volume, not secrecy). Reachable via a future publications-intent route (rebuild). |
| **Boost mechanics** | One **shared pure helper `decay_for(row, now)`** called IDENTICALLY at both boost sites — `_rerank._score` (`retriever.py:200`) and the fusion boost (`:345`) — with `now` threaded in (not a param today). Boost is a **post-RRF multiplier on the fused score only** — never touches `sem`/`kw` fetch or pool membership (preserves "liftable from one leg"). Hazard: rerank is an admin kill-switch (`:141`) and the two sites compute boost **independently**, so the single shared helper + a test that decay is identical with rerank ON and OFF is **mandatory**. |
| **Chunking compat (M2)** | When chunking lands, chunks must inherit the parent page's `type` + dates (`parent_id`/`root_id` already in schema) or decay won't apply per-chunk. |

### 6.1 Exact scoring (RAG review, folded 2026-06-25)

**News multiplier — composed, FLOOR is a HARD invariant (not tunable to 0):**
`mult = max(FLOOR, type_prior * 0.5 ** (age_days / HALF_LIFE))`, `type_prior=0.85`, `HALF_LIFE=180`,
`FLOOR=0.5` on the COMPOSED value (effective floor 0.5, not 0.85×0.5). `now = datetime.now(timezone.utc)`;
`age_days` from `metadata.published_at`. **Undated news** (the most common row) → `type_prior`, NO decay.
Future-dated → clamp age to 0. The floor guarantees a stale-but-strong news item is *demoted, never erased* =
served, not withheld — so the floor is **not** an admin-tunable knob (unlike `event_boost`/`exclude_types`).

**Event gate — fail-closed:** boost (1.2×, event_info's level) only when upcoming: `event_end >=
start_of_today_utc`; if `event_end` absent, fall back to `event_start`; if neither → **no boost** (treat as
past) + news-style decay on `published_at` if present. Compare against **start-of-day UTC** (an event ending
today is still upcoming). Recurring series are captured as a single literal date and decay as past — accepted
for the pilot, flagged.

**One shared `decay_for(row, now)` helper** called identically at BOTH boost sites (`retriever.py:200` rerank,
`:345` fusion), with `now` threaded in. Boost is a **post-RRF multiplier on the fused score only** — never
touches `sem`/`kw` fetch or pool membership (preserves "liftable from one leg").

**exclude_types is admin-overridable:** `DEFAULT_EXCLUDE_TYPES` (`:56`) is only the fallback;
`retriever.exclude_types` setting (`:139`) overrides it live. Dropping `webpage`/`office_page` from the code
default is INERT if that setting row exists → the migration must also clear/update the live setting. `news`/
`event` are new types in no exclude list → served by default (intended).

**Honest limits (so tests don't assert the impossible):**
- Fresh news is rankable **on a topical query**, not arbitrary: the decay/prior only re-orders items already
  in the top-40 fused pool (`MIN_POOL_SIZE`, `:61`) — it cannot lift a sub-40 item in. The deferred "what's
  new" digest is what pulls fresh news to the top of a generic query.
- Until the publications-intent route ships, publication titles are reachable ONLY via the `item_types`
  whitelist API, NOT free-text chat — accepted + tracked (in-corpus + embedded + reachable by explicit API =
  a missing route, not a hidden row; not withholding).

The **publications-intent route** and the **"what's new" digest** are deferred (§1 non-goals), called out
loudly so they're not silently dropped (review-against-plan).

---

## 7. Data model

- New `knowledge_items.type` values: **`news`**, **`event`** (the `type` column is free-text; no schema
  migration). `event_info` retained for GSA-curated.
- New `metadata` keys: `published_at`, `event_start`, `event_end`, `source_updated_at` (all optional).
- Orgs: reuse existing YWCC subtree (ids 4/5/6/7/10). The college entry point files under `ywcc`; each dept
  entry point under its dept org.
- **Event sources stay distinct** (owner, 2026-06-25): dashboard **post** events live in the `posts` system
  (publishing, immortal) — the crawler NEVER writes there; crawled events → `knowledge_items type='event'`;
  GSA-curated → `type='event_info'`.

---

## 8. Migration / G7

Mostly **additive** — there is almost no existing crawler prose to replace.
- **Preserve** the 27 `source='dashboard'` policy rows (manual; hard invariant) and ALL `explore.py`
  people-side rows (`created_by='crawler'`). The prose crawler's distinct `created_by='ywcc_crawl'` means it
  cannot touch either.
- The only "replace" is the prose crawler's own idempotent re-runs (content-hash version-bump) and an
  optional `--reset` scoped to `created_by='ywcc_crawl'`.
- Supersession of a dashboard row is **not** detected in-crawler (the crawler makes no cross-`created_by`
  usage decisions). Instead a **manual post-hoc review query** lists dashboard rows whose topic overlaps new
  crawl rows, for the owner to retire by hand. (No automatic detection — that would be a usage decision.)
- All writes gated: `hardened_backup` + dry-run default + `--commit`; dev-copy-first; then `embed_all.py`.

---

## 9. Heads-up barrier removal (folded in)

Per owner 2026-06-25 (`feedback_remove_headsup_barrier`): remove the two `apply_headsup` call sites in
`bot/core/message_handler.py` (lines ~513 live-fallback, ~746 main answer) and retire `bot/core/headsup.py`
+ `bot/tests/test_headsup.py`. No money / I-20 / student-info caution is appended; answers stand on the
source link. Needs a bot restart. Batched with this build. (CLAUDE.md already updated.)

---

## 10. Testing (TDD)

Parser/engine (prose crawler):
- Host+path scoping: a `cs.njit.edu` seed never yields a `computing.njit.edu` / `people.njit.edu` URL.
- URL partition: people-listing paths skipped; an inline name on a program page is kept.
- Mechanical typing: `/news/…`→news, `/events/…`→event, else policy; safe degrade on ambiguous paths.
- Date extraction: `<time>`, `article:published_time`, JSON-LD `Event` → correct ISO values; missing → absent.
- Idempotency: unchanged page skipped; changed page version-bumps; `--reset` scoped to `ywcc_crawl`.
- Anti-fab: no Person created from prose; figures captured literally, never described.
- Source safety: prose writes `created_by='ywcc_crawl'`; a people-reconcile pass leaves prose untouched
  (regression test that explore's sweep can't see `ywcc_crawl` rows).

Retrieval:
- News recency (TOPICAL query): fresh news rankable; stale news demoted below policy but still retrievable on
  a direct query (floor honored = not withheld). Decay **identical with rerank ON and OFF** (kill-switch
  divergence test). Undated news → type_prior, no decay.
- Event gate fail-closed: boost only when upcoming (`event_end`, else `event_start`, ≥ start-of-day UTC);
  past/dateless event unboosted.
- `webpage` served at a `<1.0` prior (real downweight); `office_page` removed from exclude; publications still
  excluded (reachable only via `item_types` whitelist until the deferred route).
- Scoping guard: a subdomain seed never yields an off-host URL. People skip: `/faculty` skipped,
  `/faculty-handbook` kept (segment match).

Plus: add YWCC verification questions to `eval/questions.txt` (grow-the-suite rule).

---

## 11. Goals checklist (shipped vs deferred) — for the review-against-plan gate

| goal | status |
|---|---|
| Office-style "grab everything" prose for YWCC (programs/students/news/events) | **build** |
| People + research layer preserved unchanged | **build** (reuse explore.py) |
| Granular entry points (college + each dept), independently recrawlable | **build** |
| News/event typed separately + dates captured | **build** |
| News served + recency-ranked (not withheld); event boost upcoming-only | **build** (retriever slice) |
| webpage downweight (**<1.0 prior**); office_page cleanup | **build** (retriever slice) |
| Heads-up barrier removed (code) | **build** |
| Publications-intent route | **DEFERRED → rebuild** |
| "What's new / latest news" digest intent | **DEFERRED → rebuild** |
| M2 chunking | **DEFERRED → separate project** (compat only here) |
| ND6 prose departure reconcile | **DEFERRED-with-flag** |
| Other colleges (NCE/CSLA/HCAD/MTSM) | **DEFERRED** (YWCC is the pilot) |

---

## 12. Review plan (EXPERT-REVIEW HARD GATE)

1. **RAG/LLM-researcher review** — retrieval slice (§6): news recency model, event boost gating, RRF
   interaction, withholding boundary, M2 compat. (Initial review already folded into this draft.)
2. **Senior-eng review** — correctness + efficiency of the prose engine (§4): host/path scoping, URL
   partition derivation, created_by separation, idempotency, transaction ownership.
3. Both reviewers check **against the goals in §11** (shipped vs deferred), not just diff correctness.
4. Owner sign-off → build TDD → show diff → owner signs off → commit + restart.
