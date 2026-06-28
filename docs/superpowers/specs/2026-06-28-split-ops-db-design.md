# Split Operational Data into a Separate OPS Database — Design

**Date:** 2026-06-28
**Status:** Design (awaiting owner approval, then expert review per HARD GATE)
**Owner:** Mohammad Dindoost
**Branch:** `worktree-split-ops-db` (isolated worktree; prod = `main` untouched until cutover)

> **Where this sits.** Step 1 of a 3-project stack:
> 1. **THIS — split ops DB** (de-risks the rebuild by removing the immortal-posts / org-FK burden from its scope).
> 2. DB-wipe + Crawling-2.1 rebuild (`project_durable_foundation`).
> 3. Answer-stack gate refit + cutover (`project_answer_stack_design`).
> Each is its own gated session. When this is fully done + deployed, the owner starts a **new session** for the rebuild.

---

## 1. Purpose & Goals

Move **operational / publishing / judging** data out of the knowledge DB into a separate
operational database, so the knowledge DB becomes purely the RAG/KG corpus and ops data is
isolated. This is a **flexibility / separation-of-concerns** change (owner: "this is just to add
more flexibility"), not a behavior change for users.

**Goals (each must be shipped or explicitly flagged deferred):**

- **G1.** A new **OPS DB** (`gsa_gateway_ops.db`, env `OPERATIONS_DB_PATH`) holds the full
  publishing cluster + all judging tables. The **Knowledge DB** (`gsa_gateway.db`, path unchanged)
  holds everything else.
- **G2.** **No cross-DB foreign keys.** Every FK lives wholly within one DB. Cross-DB references
  are by **stable org slug**, resolved at read time — never by rowid.
- **G3.** **EVENT → KB one-way projection (GSA-only):** a GSA event in the OPS DB derives a
  `knowledge_item` (`type=event_info`, dated, embedded) in the Knowledge DB. Idempotent +
  rebuildable; the OPS event stays immortal/authoritative, the KB copy is derived.
- **G4.** Every code touch point that reads/writes posts, deliveries, the publishing cluster, or
  judging is repointed to the OPS connection. The dashboard loads the OPS DB for its Posts tab and
  any post-based analytics.
- **G5.** A **gated, immortal-safe migration** copies every row, **verifies counts + per-table
  checksums**, passes a **fail-closed acceptance gate**, and only then drops the moved tables from
  the Knowledge DB. Reversible via `hardened_backup`.
- **G6.** No user-visible behavior change: posts publish/delete, WorldCup posts, judging flows, and
  event answers all work exactly as before.

**Non-goals (explicitly out of scope this project):**

- The DB-wipe + Crawling-2.1 rebuild (separate project).
- Any change to retrieval ranking / answer composition. The EVENT→KB projection **reproduces**
  today's `events → event_info knowledge_item` behavior across a DB boundary; it does not alter
  how anything is retrieved or answered. → **senior-eng review required; RAG review NOT required**
  (no retrieval/answering change).
- Splitting the OPS DB further (e.g. judging into its own file). One OPS DB for now.
- Moving analytics. `questions` / `response_feedback` / `conversation_stats` (and `events_log` /
  `admin_actions`) are defined in `bot/services/database.py` and **stay in the Knowledge DB** —
  this is a no-op (LLM-interaction logs belong with the knowledge/answer layer).

---

## 2. Current State (verified against code, 2026-06-28)

**Tables in `gsa_gateway.db`** (defined in `v2/core/database/schema.py` unless noted):

| Layer | Tables |
|---|---|
| Knowledge/KG (STAY) | `organizations`, `knowledge_items`, `knowledge_chunks*` + vectors + FTS, `nodes`, `edges`, `frontier`, `page_nodes`, `raw_pages`, `settings`, `schema_migrations` |
| Analytics (STAY — defined in `bot/services/database.py`) | `questions`, `response_feedback`, `conversation_stats`, `events_log`, `admin_actions`, `jobs` (`bot/services/jobs.py`) |
| Publishing cluster (MOVE) | `posts`, `post_deliveries`, `post_templates`, `events`, `event_reminders` |
| Judging (MOVE) | `judging_events`, `judging_judges`, `judging_presenters`, `judging_scores`, `judging_audience_votes`, `judging_score_audit` |

**Why the whole publishing cluster moves together** (FK closure — moving only `posts` would create
cross-DB FKs, violating G2):

- `post_deliveries.post_id → posts(id)` (ON DELETE CASCADE)
- `event_reminders.post_id → posts(id)` and `event_reminders.event_id → events(id)`
- `events` is the structured source that already derives today's `event_info` knowledge_item
- `post_templates` materializes into `posts`

**Judging** is fully self-contained (every FK is judging→judging); cleanest to relocate as a unit.

**Cross-layer references today** (every org-referencing moved table — verified against the live DB):

- `posts.org_id → organizations(id)` (NOT cascade — posts outlive orgs). 427 live rows, all org `gsa`.
- `events.org_id → organizations(id)` (optional). 2 live rows, all `gsa`.
- `post_templates.org_id → organizations(id)` **NOT NULL** (`schema.py:106`). 2 live rows, all `gsa`.
  **(Caught in senior-eng review — was missed in the first draft; all three get `org_slug`.)**
- `settings.org_id → organizations(id)` — **settings stays in Knowledge DB, so this FK is unaffected.**
- Judging has **no** FK to `organizations` (org-agnostic — verified: zero `REFERENCES organizations`
  in any `judging_*` table).
- One-way derive: `_create_event()` (`local_server.py:922`) inserts `events` + `event_info`
  knowledge_item + `event_announcement` post + `event_reminders` in one transaction today; **and**
  `_post_post(add_to_kb)` (`local_server.py:911`) writes a `post` + a `knowledge_item` in one
  transaction. After the split BOTH become cross-DB writes: cluster rows → OPS, derived
  knowledge_item → Knowledge.

**⚠ `events` is the v1 NON-STRICT table, dual-defined (verified live).** The live `events` table is
created by `bot/services/database.py:110` (`init_tables`) — `INTEGER PRIMARY KEY AUTOINCREMENT`, two
legacy columns the v2 DDL lacks (`announcement_sent`, `channel_posted`), and `org_id`
ALTER-appended **last**. It is registered in `sqlite_sequence`. The STRICT v2 `events` DDL in
`schema.py:146` is **dead code** (the `IF NOT EXISTS` no-ops because the v1 table already exists).
Consequences for this project: (a) the OPS `events` table must reproduce the **live** shape, not the
v2 DDL; (b) the migration must do an **explicit column-mapped copy** (never `SELECT *`) and seed
`sqlite_sequence`; (c) this project **resolves the dual-definition** — `events` is created **once**
by `create_ops_schema` (live shape + `org_slug`), and the two old creators are removed from the
Knowledge path (see Touch Points HIGH-3).

**Connection plumbing today:**

- `get_connection(db_path)` (`v2/core/database/schema.py`) — sets row_factory, `foreign_keys=ON`,
  `busy_timeout`, loads sqlite-vec.
- `bot/config.py` → `database_path = os.getenv("DATABASE_PATH", "./gsa_gateway.db")`.
- `v2/local_server.py` → `DB_PATH = REPO_ROOT / "gsa_gateway.db"`; fresh connection per request;
  serves the whole DB to the browser at `/db` (sql.js).
- `SchedulerRunner` opens one connection in `start()` and reuses it; passes it to `PostPublisher` /
  `PostDeleter` / `ConnectorRegistry`.
- `JudgingSessionManager._conn()` opens a fresh `sqlite3.connect(db_path)` per call.

---

## 3. Architecture

### 3.1 Two databases, two connections (no ATTACH)

- **Knowledge/LLM DB** — `gsa_gateway.db` (path unchanged so nothing else moves).
- **OPS DB** — `gsa_gateway_ops.db`, resolved from `OPERATIONS_DB_PATH`
  (default: sibling of the knowledge DB, i.e. `./gsa_gateway_ops.db`).

We use **two independent connections**, not SQLite `ATTACH`. Rationale: cross-DB ATTACH
transactions are fragile (no atomic commit across files, WAL interactions, vec extension), and we
*want* the EVENT→KB step to be a deliberate one-way derive — not an implicit shared transaction.

`schema.py` splits its DDL into two builders:

- `create_knowledge_schema(conn)` — current tables minus the moved cluster.
- `create_ops_schema(conn)` — the moved cluster + judging. The OPS `events` table reproduces the
  **live v1 shape** (AUTOINCREMENT + `announcement_sent`/`channel_posted`) **plus `org_slug`** — it
  does **not** adopt the dead STRICT v2 DDL (see §2). The monolithic `create_all` is **retired** from
  every Knowledge-DB startup path and replaced by these two builders (see Touch Points HIGH-3).

A small helper `get_ops_connection(path)` mirrors `get_connection` **without** loading sqlite-vec
(OPS has no vectors) — keeps the OPS connection lean and avoids requiring the extension wherever
only ops data is touched.

### 3.2 Cross-DB org reference = stable slug (G2)

On the OPS side, `posts`, `events`, **and `post_templates`** carry **`org_slug TEXT NOT NULL`** — the
durable join key.

- `org_id` is **retained as a plain informational INTEGER** (no FK) so no data is lost and historical
  rows keep their original id. The **contract** join key is `org_slug`.
- Reads that need org details resolve `org_slug → organizations` against the Knowledge DB.
  `resolve_org(kb_conn, slug)` returns the **full org row (including `id`)** and is **cached per
  scheduler tick** — resolved once per org per tick, never per-setting (avoids an N+1 on the publish
  hot path, where `build_post` does ~5 settings reads/post — MED-7).
- **`organizations.slug` is only `UNIQUE(parent_id, slug)`, not globally unique** (`schema.py:54`).
  Today no global slug collision exists and only `gsa` (root) is referenced, so it is safe — but
  `resolve_org` **fails loudly on >1 match**, and the migration acceptance gate asserts global
  uniqueness for every referenced slug (LOW-11). This makes the slug contract an enforced invariant.
- **Settings reads use the resolved `id`, not a stored rowid.** This is NOT a G2 violation: the
  durable cross-DB reference stored in moved tables is the slug; the `id` is obtained at read time
  from `resolve_org`. The retained `org_id` column is informational only, never the contract key.
- Writers set `org_slug` at enqueue/create time. The migration back-fills `org_slug` for every
  existing `posts`/`events`/`post_templates` row from `organizations`.

`judging_*` needs no org reference (unchanged).

### 3.3 EVENT → KB one-way projection, GSA-only (G3)

`derive_event_kb(ops_conn, kb_conn, *, org_slugs=("gsa",))` — a small, idempotent, **rebuildable**
function:

- **Source:** GSA `events` rows in the OPS DB (the structured event record — name/date/time/
  location — exactly what produces `event_info` today). Scope filter: `org_slug IN org_slugs`
  (GSA-only now; the param is the single extension point if clubs are added later).
- **Target:** an `event_info` `knowledge_item` in the Knowledge DB, with
  `metadata = {derived_from: "ops_event", org_slug, ops_event_id, date, time, ...}`.
- **Stable derive key** (so re-runs upsert, never duplicate, and the rebuild re-derives cleanly):
  `(org_slug, event natural key)` where the natural key = normalized `name` + `date`. Stored in
  metadata; the upsert matches on it. `ops_event_id` is stored as an informational value only
  (it is an OPS rowid — never used as the cross-DB contract key, per G2).
- **Transition reconciliation (MED-8):** today's `event_info` rows were written by `_create_event`
  keyed implicitly on `metadata.event_id` (an OPS rowid), **not** the new natural key. So the
  migration must **back-fill the new derive key onto the existing `event_info` rows** (match each by
  its stored `event_id` → the corresponding OPS event → compute + write the natural key). The derive
  upsert then matches on **either** `ops_event_id` **or** the natural key during the transition, so
  the 2 existing rows are recognized and **not duplicated**. A test asserts: re-deriving over the
  migrated DB yields zero new `event_info` rows.
- **Idempotent + reconcilable:** running it is safe repeatedly; an event removed/renamed in OPS
  deactivates (`is_active=0`) its stale derived item (mirrors existing reconcile semantics).
- **Embedding:** the derived `knowledge_item` is embedded by the existing `embed_all.py` pass
  (resumable, only embeds items missing a vector) — identical to how `event_info` items embed
  today. The dashboard create-event flow can opt to embed that single item inline for immediacy
  (consistent with the existing "run embed after KB add" UX).
- **Trigger points:** (a) the dashboard `_create_event()` calls `derive_event_kb` for the new GSA
  event after the OPS write commits; (b) a standalone gated `scripts/derive_event_kb.py` re-derives
  all GSA events (used by the future rebuild and as a repair tool).

This **reproduces existing behavior** — events already become `event_info` KB. No retrieval or
answer-composition code changes.

### 3.4 Components that hold both connections (corrected per review — HIGH-4/5, MED-6)

The first draft under-counted this. **Anything that enqueues or publishes a post needs BOTH
connections**, because enqueue validates the org and resolves the slug against `organizations`
(Knowledge) while writing the post (OPS), and publishing reads `settings` (Knowledge) while writing
`post_deliveries` (OPS). The true two-connection set:

1. **Scheduler stack** (`scheduler_runner`): `Scheduler` (templates/reminders/publish-due), `PostPublisher`,
   `PostDeleter` → **OPS** for posts/deliveries; **`SignatureService`** and `PostPublisher`'s
   platform/channel defaults → **Knowledge** (`settings`); **`ConnectorRegistry.conn` → OPS** (it
   writes `post_deliveries`).
2. **`enqueue_post`** (`sources.py`) — validates `organizations.is_active` + resolves `org_slug`
   (Knowledge) and inserts the post (OPS). So `SourceRunner`, the **WorldCup `match_watcher`**
   (also reads `settings` via `auto_delete_hours` + resolves org by slug), and the **fixtures digest**
   all hold both. `bot/main.py` must pass both paths to the watcher (today it passes a hardcoded
   `"gsa_gateway.db"` at `:236`).
3. **Dashboard server (`local_server.py`)** — routes each endpoint to the right DB (below) and does
   the cross-DB event/post→KB derives.

Holds exactly one: judging → **OPS** only; retriever / router / embedder → **Knowledge** only.

**Cross-DB write ordering (MED-9).** Both `_create_event` and `_post_post(add_to_kb)` write OPS
cluster rows + a Knowledge `knowledge_item`. Order: **commit OPS first, then write the derived
Knowledge row.** If the Knowledge write fails, the OPS post/event still stands (immortal/authoritative)
and the derived KB item is **rebuildable** via `derive_event_kb` (a logged warning + the standalone
re-derive script close the gap) — so a partial failure never loses operational data and never leaves
an un-rebuildable KB hole. The reverse order is forbidden (a KB item with no backing OPS row).

---

## 4. Code Touch Points

| File | Change |
|---|---|
| `v2/core/database/schema.py` | Split DDL into `create_knowledge_schema` / `create_ops_schema`; add `get_ops_connection` (no vec load); OPS `posts`/`events`/`post_templates` get `org_slug`; OPS `events` matches the **live v1 shape**. Keep `create_all` only as a thin "both schemas" helper for tests. |
| **`bot/services/database.py`** **(HIGH-3)** | **Stop creating `events` (and any moved table) on the Knowledge path.** Remove `events`/`events_log`-adjacent moved-table DDL from `init_tables`; `events` is owned by `create_ops_schema`. Prevents silent re-creation of the dropped table in the Knowledge DB. |
| **`v2/local_server.py` startup** **(HIGH-3)** | `main()` currently calls `create_all(DB_PATH)` (`:1043`) which self-heals **all** v2 tables incl. moved ones onto the Knowledge DB every server start. Replace with `create_knowledge_schema(DB_PATH)` + `create_ops_schema(OPS_PATH)`. |
| `bot/config.py` | Add `operations_db_path = os.getenv("OPERATIONS_DB_PATH", <sibling of database_path>)`. |
| `bot/main.py` | Open both paths; pass OPS path to scheduler + judging + **WorldCup watcher** (replaces hardcoded `"gsa_gateway.db"` at `:236`); Knowledge path to retriever. |
| `v2/integration/scheduler_runner.py` | Open OPS conn (posts/deliveries) + Knowledge conn (settings/orgs); pass both to `Scheduler`, `PostPublisher`, `PostDeleter`, **`SignatureService`** (Knowledge), and set **`registry.conn = OPS`** (delivery logging). |
| `v2/core/publishing/publisher.py` | Posts/deliveries → OPS conn; platform/channel defaults from `settings` → Knowledge conn via per-tick `resolve_org`. |
| `v2/core/publishing/signature.py` (`SignatureService`) | Reads `settings` → Knowledge conn. |
| `v2/core/connectors/registry.py` | `ConnectorRegistry.conn` writes `post_deliveries` → OPS conn. |
| `v2/core/publishing/deleter.py` | Posts/deliveries → OPS conn. |
| `v2/core/publishing/sources.py` (`enqueue_post`) | Takes **both** conns (or pre-resolved slug + OPS conn): validate org + resolve `org_slug` → Knowledge; insert post → OPS. |
| `v2/core/publishing/scheduler.py` | `materialize_templates` (reads `post_templates.org_slug`) / `materialize_event_reminders` → OPS conn; org/settings stamping via Knowledge resolve. |
| `v2/integration/match_watcher.py` (WorldCup) | Both conns: org-by-slug + `auto_delete_hours`(`settings`) → Knowledge; enqueue posts → OPS. |
| `v2/core/judging/db.py`, `session.py` | Open OPS conn (`_conn()` → ops path). Self-contained. |
| `v2/local_server.py` (endpoints) | Open both; posts/judging/event endpoints → OPS; KB/people/settings → Knowledge; `_create_event` + `_post_post(add_to_kb)` write cluster → OPS then derive `knowledge_item` → Knowledge (OPS-commit-first ordering, §3.4); serve OPS at `/db-ops`. |
| `dashboard/app.js` | Load a second sql.js DB from `/db-ops`; **Overview** (`:873-879`), **Posts**, and post-based **Analytics** (`:1885+`) queries read the OPS handle; KB/People/Settings read `/db`. Thread two `db` handles into `renderOverview`/`renderPosts`/`renderAnalytics`. (Judging already uses live `/judging/*` APIs — server-side repoint only.) |
| `v2/core/publishing/__init__` + callers | Thread the OPS/Knowledge conn pair where a single `conn` was passed. |

A new module `v2/core/publishing/event_projection.py` houses `derive_event_kb` + `resolve_org`.

---

## 5. Migration (G5) — gated, immortal-safe

Script: `scripts/split_ops_migrate.py` (dry-run default; `--commit` to write). Follows the project's
gated-write convention: `hardened_backup(...)` of the live DB first; reversible.

**Procedure:**

1. **Backup** the live `gsa_gateway.db` (hardened_backup: online-backup API + integrity check).
2. **Create** `gsa_gateway_ops.db` with `create_ops_schema` (OPS `events` = live v1 shape + `org_slug`).
3. **Copy** each moved table with an **explicit column-mapped INSERT** (never `SELECT *` — HIGH-2):
   - Preserve `id` values exactly (immortal rowid stability for `posts`/`post_deliveries`).
   - For `posts`/`events`/`post_templates`, back-fill `org_slug` from `organizations` (fail if any
     `org_id` has no slug — see gate). Keep `org_id` as the informational column.
   - `events` carries its legacy `announcement_sent`/`channel_posted` through unchanged (no loss).
   - **Seed `sqlite_sequence`** for the AUTOINCREMENT `events` table (`max(id)`), so future inserts
     don't collide.
4. **Verify (evidence-before-claim):**
   - Row counts match per table (Knowledge source vs OPS destination).
   - **Per-table content checksum matches** for `posts` and `post_deliveries` (immortal) and all
     judging + event tables. SQLite has **no built-in md5** → compute the checksum **in Python**
     (sha256 over each row's ordered, type-normalized column tuple; deterministic `ORDER BY id`).
   - `org_slug` resolves for **100%** of `posts`/`events`/`post_templates` rows.
   - **Global slug-uniqueness** holds for every referenced slug (LOW-11) — abort if any slug maps to
     >1 org.
   - FK integrity check passes inside the OPS DB (`PRAGMA foreign_key_check`).
5. **Fail-closed acceptance gate:** if ANY check fails → abort, drop nothing, report the diff. The
   OPS DB is left for inspection; the Knowledge DB is untouched.
6. **Only after the gate passes:** drop the moved tables from `gsa_gateway.db` (within the same
   `--commit` run, after a second confirmation that OPS counts/checksums still hold). The
   `hardened_backup` is the rollback path. **Pre-drop guard:** the migration also confirms the
   startup-schema repoints (HIGH-3) are in place, so the next process start won't re-create the
   dropped tables in the Knowledge DB.
7. **Back-fill the EVENT derive key** onto existing `event_info` rows (MED-8) so a subsequent
   `derive_event_kb` recognizes them and does not duplicate. Assert: re-derive yields 0 new rows.

**Reversibility:** worst case = restore the `hardened_backup` snapshot (whole old DB) and delete the
OPS file. No row is dropped from the source until its exact copy is checksum-proven in OPS.

**Two-file backup (was Q3, now in-scope — LOW-12):** after cutover the OPS DB holds the immortal
posts, so the backup story must cover it. `hardened_backup` + its rotation and `scripts/restart.sh`
learn about the second file as part of this project — an un-backed-up OPS DB would undermine the
immortal-posts guarantee.

**Cutover (owner-gated):** the live migration + service restart is a **production write** → owner
runs/approves it. Candidate for its own session if the build lands earlier.

---

## 6. Build Order (each gated, TDD)

1. **Schema split + config plumbing** (additive; `create_knowledge_schema`/`create_ops_schema`,
   `get_ops_connection`, `operations_db_path`). OPS `events` = live v1 shape + `org_slug`;
   `posts`/`post_templates` get `org_slug`. **Retire `create_all` from Knowledge startup paths**
   (`local_server.main`, `bot/services/database.py` `events`) — HIGH-3. Tests: both schemas build;
   OPS conn has no vec dep; **invariant test — Knowledge schema contains none of the moved tables**.
2. **Repoint subsystems** to the two-connection model (scheduler stack incl. `SignatureService` +
   `registry.conn`, publisher, deleter, `enqueue_post` (both conns), judging, WorldCup (both conns));
   `resolve_org` per-tick cache. Tests: posts publish/delete, enqueue, judging flows operate against
   a two-DB fixture; settings resolved via Knowledge DB; no writer touches a moved table on Knowledge.
3. **EVENT→KB derive** (`event_projection.py`) + dashboard `_create_event` cross-DB write +
   `scripts/derive_event_kb.py`. Tests: idempotent upsert, GSA-only scope, reconcile of removed
   events, derive key stability.
4. **Dashboard `/db-ops` + app.js two-DB load.** Tests: Posts tab + post-analytics read OPS; KB
   reads Knowledge; manual smoke.
5. **Gated migration script** (`split_ops_migrate.py`) with the copy→verify→gate→drop flow + the
   acceptance-gate unit tests (counts/checksum/slug-resolve/FK-check; fail-closed proven).

Each step: subagent-driven TDD (Sonnet runners), reviewer per cost-tiering, show the diff.

---

## 7. Testing Strategy

- **Unit:** schema builders; `resolve_org`; `derive_event_kb` (idempotency, scope, reconcile);
  acceptance-gate checks (each failure mode aborts).
- **Integration:** a two-DB fixture (temp Knowledge + temp OPS). Exercise: enqueue → publish →
  deliver → delete a post; create a GSA event → derive → retrievable `event_info`; full judging
  flow (create event → judges → presenters → score → leaderboard → audit).
- **Migration:** build a fixture Knowledge DB with seeded posts/deliveries/judging/events → run the
  migrate in dry-run (no writes) then `--commit` on a copy → assert counts + checksums identical,
  source tables dropped, `foreign_key_check` clean, rollback restores.
- **Regression:** existing judging suite (49+ tests) and publishing tests pass against the OPS path.
- **No-regression on retrieval:** event answers unchanged (the derived items are byte-identical to
  today's `event_info`).

---

## 8. Risks & Mitigations

- **R1 — immortal post loss during move.** Mitigated by copy→checksum-verify→drop-last + hardened
  backup + fail-closed gate. Drop is the final step, never before proof.
- **R2 — dangling org reference across DBs.** Mitigated by `org_slug` contract + migration gate
  requiring 100% slug resolution; `org_id` retained as informational fallback.
- **R3 — moved tables silently re-created in the Knowledge DB on startup (HIGH-3, the most dangerous
  gap).** Two paths run `CREATE TABLE IF NOT EXISTS` over moved tables against the Knowledge DB every
  start: `bot/services/database.py:110` (`events`) and `v2/local_server.py:1043` (`create_all` →
  all v2 tables). **A naive drop is futile** — the tables reappear empty and stray writes succeed
  into the wrong DB. **Mitigation (required, in build step 1/2):** retire `create_all` from the
  Knowledge startup path (use `create_knowledge_schema`), and stop `init_tables` from creating
  `events`. Only then is "the table is gone, a stray writer fails loudly" actually true. An
  **invariant test** asserts the Knowledge DB has none of the moved tables after `create_knowledge_schema`.
- **R4 — partial repoint (some writer still hits the wrong DB).** Mitigated by a grep/audit of every
  `posts`/`post_deliveries`/`judging`/`events`/`post_templates` access (build step 2 enumerates them)
  + integration tests on the two-DB fixture + the R3 invariant test.
- **R5 — dashboard sql.js two-DB drift.** Overview/Posts/Analytics read `/db-ops`; writes still go
  through live POST endpoints (server-side, correct DB). Low risk; smoke-tested.
- **R6 — two-file backup/ops burden.** Now in-scope (LOW-12): `hardened_backup` rotation + `restart.sh`
  cover the OPS file from cutover, since it holds the immortal posts. (Further split into a judging DB
  stays out of scope.)

---

## 9. Goals Checklist (fill at PR close)

- [ ] G1 OPS DB holds publishing cluster + judging; Knowledge DB holds the rest.
- [ ] G2 No cross-DB FK; org referenced by slug.
- [ ] G3 EVENT→KB GSA-only derive, idempotent + rebuildable + embedded.
- [ ] G4 All touch points repointed; dashboard loads OPS for Posts/analytics.
- [ ] G5 Gated migration: copy→verify(counts+checksum)→fail-closed gate→drop-last; reversible.
- [ ] G6 No user-visible behavior change.
- [ ] Deferred (flag loudly if any): further OPS split; non-GSA event projection; analytics move.

---

## 10. Reject Criteria (any → STOP, surface to owner)

1. Any immortal `posts`/`post_deliveries` row not exactly reproduced (count or checksum) in OPS.
2. Any cross-DB foreign key remaining after the split (incl. `post_templates.org_id`).
3. Any `posts`/`events`/`post_templates` row whose `org_slug` fails to resolve.
4. Retrieval/answer behavior for events changes (it must not).
5. Migration that drops a source table before its OPS copy is checksum-verified.
6. Any moved table re-creatable in the Knowledge DB after cutover (the R3 invariant test must pass).
7. A `derive_event_kb` re-run over the migrated DB creating duplicate `event_info` rows.

---

## 11. Open Questions (resolve before/at build)

- **Q1.** Inline-embed the derived `event_info` item in the dashboard create-event flow, or rely on
  the next `embed_all` pass? (Lean: inline for the single item, consistent with existing UX.)
- **Q2.** Default `OPERATIONS_DB_PATH` exact value — sibling `./gsa_gateway_ops.db` (proposed) vs a
  `data/` subdir. (Lean: sibling, matches `gsa_gateway.db`.)
- **Q3. RESOLVED (senior-eng review):** `restart.sh` + `hardened_backup` rotation cover the OPS file
  **from the start** (in-scope, build step 5/cutover) — an un-backed-up OPS DB would undermine the
  immortal-posts guarantee. No longer an open question.

---

## 12. Senior-Eng Review (2026-06-28) — verdict CHANGES-REQUIRED, all folded

Reviewer verified every claim against the live DB. Verdict CHANGES-REQUIRED; all 12 findings accepted
and folded above (the orchestrator independently re-verified HIGH-1/2/3 against the live DB before
folding). Summary:

- **HIGH-1** `post_templates.org_id` FK was missed → now gets `org_slug` (§3.2, touch points, gate).
- **HIGH-2** live `events` is the v1 NON-STRICT/AUTOINCREMENT table with legacy columns; OPS `events`
  matches live shape, migration uses column-mapped copy + seeds `sqlite_sequence` (§2, §3.1, §5).
- **HIGH-3** `bot/services/database.py` + `local_server` startup re-create moved tables on the
  Knowledge DB → retire `create_all` from the Knowledge path, stop `init_tables` creating `events`,
  add an invariant test (§3.1, touch points, R3, reject #6). Most dangerous gap.
- **HIGH-4/5** `enqueue_post` + WorldCup `match_watcher` need BOTH connections → §3.4 corrected.
- **MED-6** `SignatureService` (Knowledge) + `ConnectorRegistry.conn` (OPS) enumerated (touch points).
- **MED-7** settings read via per-tick `resolve_org`→id (no N+1; slug stays the stored contract) (§3.2).
- **MED-8** derive-key/back-fill so existing `event_info` rows aren't duplicated (§3.3, §5, reject #7).
- **MED-9** `_post_post(add_to_kb)` is also a cross-DB write; OPS-commit-first ordering (§3.4).
- **MED-10** Overview + Analytics tabs also read posts via sql.js → both repointed (touch points).
- **LOW-11** `organizations.slug` only `UNIQUE(parent_id,slug)`; `resolve_org` fails on >1 match +
  gate asserts global uniqueness for referenced slugs (§3.2, §5).
- **LOW-12** two-file backup promoted to in-scope (R6, Q3 resolved).

RAG review **not** triggered: the EVENT→KB projection reproduces existing `event_info` behavior with
no retrieval/answering change.
