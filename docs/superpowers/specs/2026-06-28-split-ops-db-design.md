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

**Cross-layer references today:**

- `posts.org_id → organizations(id)` (NOT cascade — posts outlive orgs)
- `events.org_id → organizations(id)` (optional)
- `settings.org_id → organizations(id)` — **settings stays in Knowledge DB, so this FK is unaffected.**
- Judging has **no** FK to `organizations` (org-agnostic).
- One-way derive: `_create_event()` (dashboard) inserts `events` + `event_info` knowledge_item +
  `event_announcement` post + `event_reminders` in one transaction today. After the split this
  transaction is split: cluster rows → OPS, derived knowledge_item → Knowledge.

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
- `create_ops_schema(conn)` — the moved cluster + judging, with the OPS-side schema below.

A small helper `get_ops_connection(path)` mirrors `get_connection` **without** loading sqlite-vec
(OPS has no vectors) — keeps the OPS connection lean and avoids requiring the extension wherever
only ops data is touched.

### 3.2 Cross-DB org reference = stable slug (G2)

On the OPS side, `posts` and `events` carry **`org_slug TEXT NOT NULL`** — the durable join key.

- `org_id` is **retained as a plain informational INTEGER** (no FK) so no immortal data is lost and
  historical rows keep their original id. The **contract** join key is `org_slug`.
- Reads that need org details resolve `org_slug → organizations` against the Knowledge DB
  (`SELECT id,name,... FROM organizations WHERE slug=?`). A tiny cached resolver
  (`resolve_org(kb_conn, slug)`) lives next to the publishing code.
- Writers set `org_slug` at enqueue/create time (they already know the org). The migration
  back-fills `org_slug` for every existing row from `organizations`.

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

### 3.4 Components that hold both connections

Only two:

1. **Scheduler / Publisher / Deleter** — posts/deliveries on the OPS conn; org settings
   (signatures, channels, `default.auto_delete_hours`) on the Knowledge conn via `org_slug` resolve.
2. **Dashboard server (`local_server.py`)** — routes each endpoint to the right DB (below).

Everything else holds exactly one: judging → OPS only; retriever/router/embedder → Knowledge only;
WorldCup enqueue → OPS only.

---

## 4. Code Touch Points

| File | Change |
|---|---|
| `v2/core/database/schema.py` | Split DDL into `create_knowledge_schema` / `create_ops_schema`; add `get_ops_connection` (no vec load); OPS `posts`/`events` get `org_slug`. |
| `bot/config.py` | Add `operations_db_path = os.getenv("OPERATIONS_DB_PATH", <sibling of database_path>)`. |
| `bot/main.py` | Open both connections/paths; pass OPS path to scheduler + judging; Knowledge path to retriever. |
| `v2/integration/scheduler_runner.py` | Open OPS conn (posts) + Knowledge conn (settings/orgs); pass both down. |
| `v2/core/publishing/publisher.py` | Read/write posts/deliveries on OPS conn; resolve org settings via Knowledge conn + `org_slug`. |
| `v2/core/publishing/deleter.py` | Posts/deliveries on OPS conn. |
| `v2/core/publishing/sources.py` (`enqueue_post`) | Write posts on OPS conn; set `org_slug`. |
| `v2/core/publishing/scheduler.py` | `materialize_templates` / `materialize_event_reminders` on OPS conn. |
| `v2/integration/match_watcher.py` (WorldCup enqueue) | Enqueue on OPS conn. |
| `v2/core/judging/db.py`, `session.py` | Open OPS conn (`_conn()` → ops path). |
| `v2/local_server.py` | Open both; route posts/judging/event-create endpoints → OPS; KB/people/settings → Knowledge; `_create_event` writes cluster → OPS then `derive_event_kb` → Knowledge; serve OPS at `/db-ops`. |
| `dashboard/app.js` | Load a second sql.js DB from `/db-ops`; Posts tab + post-based Analytics queries read the OPS DB; KB/People/Settings read `/db`. (Judging already uses live `/judging/*` APIs — server-side repoint only.) |
| `v2/core/publishing/__init__` + callers | Thread the OPS/Knowledge conn pair where a single `conn` was passed. |

A new module `v2/core/publishing/event_projection.py` houses `derive_event_kb` + `resolve_org`.

---

## 5. Migration (G5) — gated, immortal-safe

Script: `scripts/split_ops_migrate.py` (dry-run default; `--commit` to write). Follows the project's
gated-write convention: `hardened_backup(...)` of the live DB first; reversible.

**Procedure:**

1. **Backup** the live `gsa_gateway.db` (hardened_backup: online-backup API + integrity check).
2. **Create** `gsa_gateway_ops.db` with `create_ops_schema`.
3. **Copy** each moved table row-for-row into the OPS DB. For `posts`/`events`, back-fill
   `org_slug` from `organizations` (fail if any `org_id` has no slug — see gate).
4. **Verify (evidence-before-claim):**
   - Row counts match per table (Knowledge source vs OPS destination).
   - **Per-table content checksum matches** for `posts` and `post_deliveries` (the immortal tables):
     e.g. `SELECT md5/sha over ordered concatenation of all columns` computed identically on both
     sides. Judging tables: row-count + checksum.
   - `org_slug` resolves for **100%** of `posts`/`events` rows.
   - FK integrity check passes inside the OPS DB (`PRAGMA foreign_key_check`).
5. **Fail-closed acceptance gate:** if ANY check fails → abort, drop nothing, report the diff. The
   OPS DB is left for inspection; the Knowledge DB is untouched.
6. **Only after the gate passes:** drop the moved tables from `gsa_gateway.db` (within the same
   `--commit` run, after a second confirmation that OPS counts/checksums still hold). The
   `hardened_backup` is the rollback path.
7. **Re-derive** GSA `event_info` items via `derive_event_kb` (no-op if they already exist; this
   just re-points provenance) — optional, since existing `event_info` rows already live in the
   Knowledge DB and are untouched by the move.

**Reversibility:** worst case = restore the `hardened_backup` snapshot (whole old DB) and delete the
OPS file. No row is dropped from the source until its exact copy is checksum-proven in OPS.

**Cutover (owner-gated):** the live migration + service restart is a **production write** → owner
runs/approves it. Candidate for its own session if the build lands earlier.

---

## 6. Build Order (each gated, TDD)

1. **Schema split + config plumbing** (additive; `create_ops_schema`, `get_ops_connection`,
   `operations_db_path`). No behavior change. Tests: both schemas build; OPS conn has no vec dep.
2. **Repoint subsystems** to the two-connection model behind the new path (scheduler, publisher,
   deleter, enqueue, judging, WorldCup). Tests: posts publish/delete, enqueue, judging flows all
   operate against an OPS DB fixture; settings resolved via Knowledge DB.
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
- **R3 — partial repoint (some writer still hits the old DB).** Mitigated by a grep/audit of every
  `posts`/`judging` access (build step 2 enumerates them) + integration tests on the OPS fixture.
  After cutover, the Knowledge DB no longer has these tables, so a stray writer fails loudly (not
  silently) — surfaced immediately.
- **R4 — dashboard sql.js two-DB drift.** The Posts tab reads `/db-ops`; writes still go through
  live POST endpoints (server-side, correct DB). Low risk; smoke-tested.
- **R5 — two-file backup/ops burden.** Accepted trade for separation; the migration + restart docs
  note both files. (The future split-further into a judging DB stays out of scope.)

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
2. Any cross-DB foreign key remaining after the split.
3. Any `posts`/`events` row whose `org_slug` fails to resolve.
4. Retrieval/answer behavior for events changes (it must not).
5. Migration that drops a source table before its OPS copy is checksum-verified.

---

## 11. Open Questions (resolve before/at build)

- **Q1.** Inline-embed the derived `event_info` item in the dashboard create-event flow, or rely on
  the next `embed_all` pass? (Lean: inline for the single item, consistent with existing UX.)
- **Q2.** Default `OPERATIONS_DB_PATH` exact value — sibling `./gsa_gateway_ops.db` (proposed) vs a
  `data/` subdir. (Lean: sibling, matches `gsa_gateway.db`.)
- **Q3.** Whether `restart.sh` / backup rotation should learn about the second file now, or as a
  fast-follow. (Lean: include OPS in `hardened_backup` rotation from the start.)
