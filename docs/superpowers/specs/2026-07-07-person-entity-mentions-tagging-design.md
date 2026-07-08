# Person Entity-Mentions Tagging + Enriched Person Answers — Design

- **Date:** 2026-07-07
- **Branch:** `feat/processing-debt-pilot` (or a fresh `feat/entity-mentions` — build-time call)
- **Status:** REVIEWED — senior-eng (BUILD-AFTER-FIXES B1–B5) + Fable (SHIP-WITH-CHANGES) both
  folded into §15 REVISION v2 (binding). APPROVED-FOR-BUILD (Fable OK stands in per owner
  delegation). Build to §15.
- **Project:** Oracle Processing-Debt → FIX phase (`docs/research/oracle-processing-debt/`)
- **Supersedes framing in:** the resume-block "KG+KB winner-take-all fix"

---

## 1. Problem

For person/entity queries ("who is Vincent Oria?"), the bot routes to a structured KG
lookup and returns the deterministic `entity_card` VERBATIM, then **short-circuits** —
semantic RAG never runs. The card (`v2/core/retrieval/entity.py:534`) pulls only a fixed
set of ID-linked KB types (`_CARD_DOC_TYPES = about, research_statement, education,
teaching, service`) plus roles/email/phone/research-areas.

So **owned KB prose about the person is silently dropped**:
- **ID-linked but not in the card:** `award` rows (498 rows, 87 faculty) — never fetched.
- **NOT ID-linked (mention-only):** the curated bio FAQ ("Who is Prof. Vincent Oria?",
  KB id=64, `created_by='migration'`, `entity_id=NULL`), MMI-Workshop FAQs, college news
  (e.g. Fadi Deek's memoir article). These mention the person by name but carry no
  `entity_id`, so no join can find them today.

This is the measured processing-debt lever: **~85% of owned person-facts never enter the
retrieval pool**, on the bot's highest-traffic surface (person queries).

### Why prior "just search at answer time" was rejected
A query-time RAG+gate re-decides "is this prose about this person?" on every request, blind,
with no human in the loop. Worse, a naïve keyword match is unusable: `content LIKE '%Oria%'`
returns **712 rows**, ~96% false positives ("mem**oria**l", giant policy PDFs). Requiring
both names as whole words → 25, still with roster/co-author noise.

### The chosen frame (Fable's LOCATE vs ANSWER ruling)
Tags are an **index (LOCATE)**, not an **answer (ANSWER)**. Tagging makes entity prose
*findable* deterministically; it never replaces the LLM for prose comprehension
(comparisons, how-to, synthesis) nor semantic RAG for the open-vocabulary long tail.
Therefore: **tag entities offline (LOCATE), keep the LLM for ANSWER, keep semantic RAG as
the coverage floor.** This spec builds the LOCATE layer for *people*, phase 1 of a broader
(offices/colleges/topics, later) tagging vision — as a **reusable** pass, not a one-off.

---

## 2. Goals

1. **`entity_mentions` tagger (offline, gated, reusable).** A batch pass that resolves, for
   each in-scope KB item, which Person node(s) it is genuinely ABOUT, and writes
   `entity_mentions` rows (item ↔ node, with match basis). Many-to-many. Produces a
   human-reviewable **audit list** before any live effect.
2. **Enriched person answer (additive, verbatim).** The person card gains, under a labeled
   divider, (a) the person's **awards** (id-linked, deterministic) and (b) **tagged prose**
   (curated bio / news / workshop) via the `entity_mentions` join — appended VERBATIM with a
   source link, never blended or reworded by the LLM.
3. **Length-budgeted** so a Discord 2,000-char cap never truncates verbatim prose mid-text.
4. **Flag-gated** with an independent Tier-2 kill switch.
5. **RAG unchanged** — remains the coverage floor for non-entity questions.
6. **Prove it moved the debt** — a $0 cached Set-A re-run after landing.

### Non-goals (explicitly deferred / out of scope)
- **Corpus-wide topic/program/policy tagging** — the bigger vision; NOT this spec. The
  tagger is *built* reusable, but only people are tagged here.
- **Replacing or shrinking RAG / the LLM** — kept as-is (see §12 silent-miss caution).
- **Any free LLM compose over merged KG+KB context** — reopens the fabrication surface;
  forbidden. Verbatim append only.
- **FacultyFolio integration** — out of scope (owner: "don't bring FacultyFolio").
- **Students / staff surfacing** — rollout phase 2/3 (§11); phase 1 validates faculty.

---

## 3. Approach overview

```
INGEST/BATCH (offline, gated)                 SERVE (bot, additive, deterministic)
─────────────────────────────                ────────────────────────────────────
entity_mentions_tagger.py                     structured_answer.person_addendum(result, conn)
  for each in-scope KB item:                    ├─ Tier 1: id-linked awards  (SQL join, verbatim)
    resolve Person nodes it's ABOUT             └─ Tier 2: tagged prose      (entity_mentions join,
      (deterministic gate, §5)                        gated by PERSON_MENTIONS_ENABLED, verbatim)
    write entity_mentions rows                  appended under "More on this person" + source link,
  emit audit CSV for spot-check                 length-budgeted, NO llm compose
  gated hardened_backup + --commit
```

Serving change lives in the **shared** layer (`structured_answer.py`, beside
`deterministic_suffix`) so BOTH answer paths (`_answer_decision` primary and
`_try_structured` legacy) get it with no duplication — per Fable's catch that
`message_handler.py:438` is the rare legacy path.

---

## 4. Data model — `entity_mentions`

New table (STRICT), created in `v2/core/database/schema.py`:

```sql
CREATE TABLE IF NOT EXISTS entity_mentions (
    item_id      INTEGER NOT NULL,       -- knowledge_items.id
    node_id      INTEGER NOT NULL,       -- nodes.id (Person)
    node_key     TEXT    NOT NULL,       -- nodes.key (entity_id) — stable join key for serving
    match_basis  TEXT    NOT NULL,       -- 'title' | 'both_names' | 'llm_verified'
    confidence   REAL,                   -- 0..1 (basis-derived; title=1.0)
    created_by   TEXT    NOT NULL DEFAULT 'entity_mentions_tagger',
    created_at   TEXT    NOT NULL,
    PRIMARY KEY (item_id, node_id)
) STRICT;
CREATE INDEX IF NOT EXISTS idx_em_node ON entity_mentions(node_key);
CREATE INDEX IF NOT EXISTS idx_em_item ON entity_mentions(item_id);
```

- **Many-to-many** by design: the MMI-news row → Oria + Satoh + Dindoost, all rows.
- **Roster/nobody rows → zero rows** (the anti-roster gate rejects them). A 40-name faculty
  page produces NO mentions (never "the bio of all 40").
- **Derived data.** Fully rebuilt by the tagger; never authored by hand; tagger touches only
  its own `created_by` scope. `node_key` stored so serving joins survive without a lookup.
- **⚠ SUPERSEDED — build to §15 R2/R3/R9:** this table lives in the **KNOWLEDGE** schema (R2,
  not OPS); the PK/serve key is the **hybrid** `stable_key = COALESCE(natural_key,'id:'||item_id)`
  keyed to the live `is_active=1` row (R3, reconciles the versioned-crawler-row vs
  natural_key-NULL-bio split); `created_at` gets `DEFAULT (datetime('now'))` (R9). The DDL block
  above is the v1 sketch — R2/R3/R9 are binding.

---

## 5. The tagger — resolution gate

Module: `v2/core/ingestion/entity_mentions.py`. Runner: `scripts/tag_entity_mentions.py`
(gated: dry-run default, `hardened_backup`, `--commit`; `--audit out.csv`).

### 5.1 In-scope KB item types (phase 1) — SEE §15 R5/R6 (binding)
Tier-2 mention prose ONLY (verbatim types NOT already surfaced by the card):
- **INCLUDE:** curated `faq` (bio), crawler `news`, `event_info`.
- **EXCLUDE:** `award` — NOT tagged; served by direct id-link SQL in Tier-1 (R5, avoids
  dead `entity_mentions` rows). `about` — NOT tagged; already id-linked AND rendered by the
  card via `_CARD_DOC_TYPES` (R6 — my earlier "card excludes about" was FACTUALLY WRONG).
  `publication`, `profile`/`research_areas`/`teaching`/`education`/`research_statement`
  (card-covered or noise), `syllabus`, `pdf` (boilerplate). `policy`/`webpage` deferred
  (roster-heavy; revisit in a topic-tagging phase).

### 5.2 Resolution (per item, against ALL active Person nodes)
Candidate people are found by surname LIKE prefilter, then filtered by a **conjunctive,
tiered** gate (strictest first). This is deterministic and LLM-free by default
(LLM-agnostic; the naïve-substring failure is solved by structure, not a model):

1. **TITLE fast-path (confidence 1.0, basis `title`).** If a Person's full name appears
   (whole-word) in the item **title**, accept. Recovers the 42 curated bios cleanly
   ("Who is Prof. Vincent Oria?") at near-deterministic confidence.
2. **BODY gated path (basis `both_names`), ALL of:**
   a. **Both name tokens** (first + last) present as **whole words** (word-boundary regex,
      case-insensitive) — kills "mem**oria**l" and namesake-by-last-name-only.
   b. **Anti-roster (KG-grounded).** Count how many *other distinct active Person-node
      names* appear (whole-word) in the item. If the target appears **once** AND **≥ROSTER_N
      (default 5)** other known people appear → REJECT (faculty list / co-author dump).
   c. **Namesake abstain.** If **>1 active Person node shares the target full name**, require
      corroboration (a shared dept/role/org token in the item) else **append nothing** for
      this item↔person. Ambiguity → silence (mirrors `scholar_discovery.classify_candidate`).
3. **Optional LLM-verify escalation (default OFF, `EM_LLM_VERIFY=0`).** For a configurable
   borderline band, a sync `generate_json_sync` yes/no over the local window (reuses the
   area-expansion seam, `AREA_VERIFY_MODEL`-style config). Deferred-on by default to keep
   phase 1 simple, fast, and model-free; available without a rewrite (no-caps principle).

### 5.3 Outputs & safety
- Writes `entity_mentions` rows only for accepted (item, node) pairs; roster/ambiguous → none.
- **Audit CSV** (`--audit`): every accepted pair with item title, person, basis, confidence,
  + a snippet — for owner/reviewer spot-check BEFORE `--commit` matters.
- Gated write: `hardened_backup` + dry-run default + `--commit`; writes via a self-owned
  short-lived writable connection (never a passed graph-write conn — invariant).
- **Rebuildable:** re-run after any KB change; idempotent (upsert on PK, sweep stale rows
  in its own `created_by` scope).

### 5.4 Calibration set
The 25 recovered rows for Oria (from `scratchpad/kb_prose_probe2.py`) + the 712→25
false-positive corpus are the labeled gate-calibration set. Gate must: accept id=64 + the
5 MMI FAQs; REJECT the "Ph.D. Computer Science" roster page and the Scholar co-author
"Personal website" dumps. A small gold file (`scripts/eval_entity_mentions.py`) pins
precision/recall (target precision ≥ 0.9 on the labeled set).

---

## 6. Serving — enriched person answer

### 6.1 Patch site (shared layer)
New `structured_answer.person_addendum(result, conn) -> str | None`, beside
`deterministic_suffix`. Called from `_compose_structured` (or the two callers) AFTER the
verbatim/composed card + AFTER `deterministic_suffix`, so it appends last. Both answer paths
(`_answer_decision`, `_try_structured`) inherit it. Only fires when `result['skill']` is a
single-person skill (`entity_card`; extensible to `research_of_person`) AND a person entity_id
is in scope.

### 6.2 What it appends (both VERBATIM, under one divider `─── More on this person ───`)
- **Tier 1 — Awards** (`PERSON_ADDENDUM_ENABLED`): SQL for the person's id-linked `award`
  rows, rendered compactly (`<title> (<year>)`, desc-year, drop bare-`^\d{4}$` noise rows —
  mirror FacultyFolio `format_awards`), capped at top `AWARD_CAP` (default 6) with
  "+N more" overflow.
- **Tier 2 — Tagged prose** (`PERSON_MENTIONS_ENABLED`, independent kill switch): join
  `entity_mentions` for this `node_key`, prefer `basis='title'` (curated bio) then news/
  workshop by recency; take the single best item; append its content VERBATIM (source link).

### 6.3 Verbatim + length budget (Discord 2,000)
Verbatim hard line ⇒ prose is NEVER truncated mid-text. Budget order:
1. Card (always) + `deterministic_suffix` (always).
2. Awards (compact; already short).
3. **One** Tier-2 prose item: include WHOLE **iff** it fits the remaining budget; else
   render a one-line pointer + source link ("More: <title> — <source_url>"). Never a partial
   verbatim block. (Consistent with the blessed stub+link rule for over-long content.)

### 6.4 Anti-fabrication
No LLM sees the merged context. Awards + prose are appended as data, exactly like
`deterministic_suffix` appends links/metrics. The card's verbatim guarantee is untouched.

---

## 7. Flags & config (`bot/config.py`)
- `PERSON_ADDENDUM_ENABLED` (default OFF for staged rollout; flip after review) — master.
- `PERSON_MENTIONS_ENABLED` (default OFF) — Tier-2 sub-switch (kill gated prose without
  losing awards) per Fable.
- `EM_LLM_VERIFY` (default 0), `EM_ROSTER_N` (default 5), `AWARD_CAP` (default 6) — tunables.

---

## 8. Ops / freshness
- Tagger is a recurring, gated op (like crawl/embed/refresh): re-run after any KB change;
  add a dashboard "Data Sources" job later (out of scope now, CLI first).
- No bot restart needed for a DB-only tag rebuild (bots read live); the serving code change
  needs one restart on deploy.

---

## 9. Testing (TDD)
- **Gate unit tests:** title fast-path accept; both-names accept; "memorial" reject;
  roster-page reject (≥ROSTER_N other names); namesake abstain; co-author-dump reject.
- **Gold gate** (`eval_entity_mentions.py`): precision ≥0.9 on the labeled Oria set;
  id=64 + MMI accepted, roster/website rejected.
- **Serving tests:** awards render compact + capped; Tier-2 whole-if-fits vs link overflow;
  verbatim never partial; addendum fires on `entity_card` only; both answer paths append;
  flags off = byte-identical to today.
- **Regressions:** Oria answer now includes awards + bio, still correct; GSA officer/club
  answers unchanged; a namesake pair (two same-name faculty) appends nothing.
- **Grow-suite:** add Oria/Deek verification Qs to `eval/questions.txt`.

## 10. Prove-it
$0 cached Set-A re-run (oracle cache in `eval/processing_debt/.cache/`) after landing →
show the person-query owned-miss debt dropped.

## 11. Rollout
Faculty → students → staff. Phase 1 tags & surfaces all people, but validation/spot-check
focuses on **faculty** first (the award + curated-bio data lives there); students/staff
surfacing verified in later passes as their prose grows.

## 12. Risks
- **Silent miss (Fable's caution):** a pure-tag path returns nothing on an UNtagged item and
  looks confident. Mitigation: this is ADDITIVE — the card + RAG floor are untouched; a
  missing tag just means "no addendum," never a wrong/blank answer. **RAG stays.**
- **Namesake false-attach:** mitigated by the abstain rule (§5.2c). Prefer silence.
- **Re-ingest id drift:** tagger re-run after crawl (§4).
- **Length overflow:** §6.3 budget; verbatim never partial.

## 13. Open questions — ALL RESOLVED (see §15 REVISION v2)
1. Key → hybrid `COALESCE(natural_key, 'id:'||item_id)` (R3).
2. Flag defaults → awards ON, mentions OFF-until-audit (R8).
3. `about` in Tier-2 → NO, already in the card (R6).

## 14. Goals checklist (filled at build end per review-against-plan)
- [x] **G1** entity_mentions tagger (offline, gated, audit) — `entity_mentions.py` gate+build, `scripts/tag_entity_mentions.py` (dry-run/--commit/--audit, hardened_backup). Tasks 1–4.
- [x] **G2a** awards on card (Tier 1, id-linked) — `build_person_addendum` direct SQL. Task 5/6.
- [x] **G2b** tagged prose on card (Tier 2, sub-flag `PERSON_MENTIONS_ENABLED`) — entity_mentions join. Task 5/6.
- [x] **G3** length budget — `render_addendum` measured on the COMPOSED text, platform-aware (Discord 2000 / Telegram 4096), prose never partial. Task 5/6.
- [x] **G4** flags + independent Tier-2 switch — `PERSON_ADDENDUM_ENABLED` (ON) + `PERSON_MENTIONS_ENABLED` (OFF). Task 5.
- [x] **G5** RAG unchanged — no retriever/embedder edit; addendum is additive after compose. Verified.
- [ ] **G6** Set-A re-run proof — DEPLOY-step (post-merge, $0 cached). Pending live tag + flag flip.
- **DEFERRED (loud):** corpus-wide topic/office/college tagging; students/staff surfacing (phase 2/3); LLM-verify default-off; multi-item prose surfacing; dashboard "Data Sources" job.

---

## 15. REVISION v2 — BINDING (folds senior-eng BUILD-AFTER-FIXES + Fable SHIP-WITH-CHANGES)

Both reviews landed 2026-07-08. Senior-eng verdict BUILD-AFTER-FIXES (B1–B5, S1–S6); Fable
verdict SHIP-WITH-CHANGES (ruled the 3 open Qs + quality items). Where the two split (key
choice) the resolution below reconciles both concerns. **This section supersedes the body
where noted; build to THIS.**

**R1 (senior-eng B1+B2) — Serving is computed in the WORKER THREADS, not `_compose_structured`.**
`person_addendum` CANNOT take `(result, conn)` and run in `_compose_structured` — the conn and
`result` dict die inside the thread bodies (`message_handler.py:_run` :550-573, `conn.close()`
in `finally`; `_structured_from_route` :649-670; comment ":the result dict dies with the thread"
:567). Mirror `deterministic_suffix`: compute the addendum as a **STRUCTURED PAYLOAD** inside
BOTH thread bodies and return it as an extra tuple element. `_compose_structured` gains
`addendum` + `platform` params and makes the **fit decision AFTER it has the composed `out`**
(entity_card is NOT in `_DETERMINISTIC_SKILLS`, so `compose_from_rows` runs and can expand
length — the budget MUST be measured on the composed text, not `facts`). Payload shape:
`{"awards": <compact str|None>, "prose": {"title","content","url","date"}|None}`. Covers all
**THREE** entry points that reach these threads: `_answer_decision` (primary :707),
`_try_structured` (legacy :576), **and `_resume_pending`→`_structured_from_route` (:639)** — the
disambiguation-resume path the body's "both answer paths" wording missed.

**R2 (senior-eng B3) — `entity_mentions` lives in the KNOWLEDGE schema, NOT OPS.** It FK-joins
`knowledge_items.id`/`nodes.id`, both in the knowledge DB (serving conn = `db.db_path`). Register
in `_KNOWLEDGE_TABLE_DDL` + BOTH indexes in `_KNOWLEDGE_INDEXES`. (Do NOT pattern-match on
`area_expand_cache`, which is OPS precisely because it has no cross-DB join.)

**R3 (senior-eng B4 + Fable-a — the split, reconciled) — key by a HYBRID stable key.**
Store both `natural_key` and `item_id`; the served/PK key is `stable_key = COALESCE(natural_key,
'id:'||item_id)`. Rationale reconciling both reviewers: crawler rows (`news`/`event_info` — have a
natural_key, and are VERSIONED so a re-crawl gives a NEW item_id + deactivates the old → pure
item_id would silently go dark, senior-eng B4) key by natural_key and survive re-ingest; the
curated `faq` bio (id=64, `natural_key=NULL`, the headline recovery — Fable verified natural_key
CANNOT key it) falls back to item_id, which is stable because migration/manual rows are NOT
re-ingested by the crawler (Fable-a). Serving JOINs `entity_mentions ⋈ knowledge_items` on the
LIVE `is_active=1` row via `stable_key`. STILL wire the tagger-rebuild into the crawl/embed finish
step (needed to tag NEW items regardless). PK = `(stable_key, node_key)`; keep `item_id` as an
audit column. (Node side already correct: serve by stable `node_key`.)

**R4 (senior-eng B5) — read `entity_id` from `route.args`.** The thread has the Route; read
`route.args["entity_id"]` for `entity_card`/`research_of_person` — no need to surface it on the
(dead) result dict.

**R5 (senior-eng S1) — awards are NOT tagged into `entity_mentions`.** They're already id-linked;
Tier-1 serves them by direct SQL (`type='award' AND entity_id=?`). `entity_mentions` is EXCLUSIVELY
the association layer for non-id-linked mention prose. (Removes the dead-data redundancy.)

**R6 (senior-eng + Fable-c, factual) — `about` EXCLUDED from Tier-2.** It is id-linked AND already
rendered by the card (`_CARD_DOC_TYPES` includes `about`, entity.py:67/568-573). Tier-2 = curated
`faq` bio + crawler `news`/`event_info` only. (Corrects §5.1 + §13-q3.)

**R7 (senior-eng S2 + Fable + verbatim hard line) — prose is NEVER truncated; platform-aware budget.**
Budget uses the PLATFORM cap (Discord 2,000 / Telegram 4,096; min-2,000 if unknown), passed into
`_compose_structured`. Order: card (+suffix) always → awards (compact) → ONE prose item WHOLE **iff**
it fits the remaining budget; **else OMIT the prose addendum entirely** (card+awards still complete,
correct, honest-partial) with an OPTIONAL `More: <title> — <url>` pointer. **Prose is never shown
partially** → honors the "prose NEVER stubbed" hard line (a link is not partial prose; omission is
not a stub). Multi-message split = DEFERRED (loud). No LLM sees merged context (anti-fab intact).

**R8 (Fable-b) — merge flag defaults:** `PERSON_ADDENDUM_ENABLED=ON` (awards, 498/498 id-linked,
zero namesake risk — immediate win for 87 faculty), `PERSON_MENTIONS_ENABLED=OFF` until the audit
CSV is read and Tier-2 precision ≥0.9 on the labeled Oria set is confirmed, then flip.

**R9 (senior-eng S3/S4/S5/S6):** `created_at TEXT NOT NULL DEFAULT (datetime('now'))`; tagger's
writable conn sets `PRAGMA busy_timeout=5000`; serving JOIN filters `nodes.is_active=1` (a departed
person gets no addendum before the next tagger run); Tier-2 recency ordering = JOIN `knowledge_items`
and `ORDER BY` its captured date/`created_at` (no date column on `entity_mentions`).

**R10 (Fable quality) — gate calibration + cost:** anti-roster runs the surname-LIKE prefilter
BEFORE the ~1,200-name whole-word sweep. `EM_ROSTER_N=5` kept, but the gold set (§5.4) MUST pin an
ACCEPT case for a GENUINE multi-person news item (e.g. Deek's memoir names collaborators) so an
over-tight ROSTER_N is caught in test, not prod. "Single best prose item" (§6.2) = phase-1 length
cap → **DEFERRED (loud): multi-item surfacing later.**

**R11 (Fable dedup) — no verbatim dedup.** id=64 restates areas/education the card already prints;
verbatim-hard-line forbids editing it and never-withheld forbids dropping the card docs → mild
overlap is accepted as the cost of the guarantee. Place the Tier-2 bio LAST under
`─── More on this person ───`. If overlap is ever egregious in the audit, the lever is content
SELECTION (pick the single richest source), NEVER content editing.

**R12 (senior-eng no-caps) — `AWARD_CAP` is a config tunable** (default 6, justified by Discord
budget), not a bare magic constant.

**Goal §14 update:** G3 (length budget) is now SUPPORTED via R1+R7 (measured on composed text,
platform-aware, prose-never-partial) — it was the one goal asserted-but-unsupported in v1.

**Both reviewers' bottom line:** concept correct, invariant-respecting, additive/verbatim; build to
R1–R12. Fable's OK stands for this step with R1–R12 folded (done here). Senior-eng BUILD-AFTER-FIXES
= B1–B5 resolved by R1–R6/R9. → proceed to writing-plans → TDD.
