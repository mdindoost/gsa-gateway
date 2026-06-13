# Overview Review QA — Design Spec

**Goal:** Turn ad-hoc "eyeballing" of LLM-generated faculty overviews into a
**repeatable, system-assisted human-review procedure** that produces a trustworthy
quality signal per department, and drives an **iterate-until-clean** loop on the
overview prompt.

**What it validates:** that each overview is **faithful to its source** — the NJIT
profile facts it was generated from (no hallucinated/misstated claims). Overviews
are the only LLM-written faculty content left after the personal-website prune;
this is how we certify them.

---

## 1. Approach — and what we deliberately avoid

- **Human judgment is the trust signal.** Using the local `llama3.1:8b` to *judge*
  the same model's overviews is circular and unreliable (the model already
  mis-extracted "NetworkX"). An LLM may later *assist* (prioritize suspects), but
  never certify.
- **No copy-paste from NJIT.** We already have the source: the overview was
  generated from the entity's parsed facts (its other `knowledge_items`), and we
  store `source_url`. The review shows the source automatically.
- **System-assisted, human-decided:** the system picks the sample, shows the
  overview next to its source, records the verdict; the admin only reads + judges.

## 2. Sampling

- Sample pool = **active overviews** only: `type='overview' AND is_active=1`,
  grouped by **department = `org_id`** (5 CS, 6 DS, 7 Informatics). (Today: CS 56,
  DS 24, Informatics **1** — not 0.)
- **20% per department per run**, `ceil`, **minimum 1** — but a department with **0
  active overviews is skipped** (don't emit a phantom sample). A dept can have
  faculty but 0 overviews when `overview.generate()` returned `''` for lack of
  grounding (`overview.py:98`).
- Random draw, **persisted** at run creation (see §4/§6) so reopening the Review
  tab shows the same items; a re-sample after a prompt fix is a *new* run with a
  *fresh* draw. Reproducibility comes from the stored sample, not a seed.

## 3. Review surface (dashboard — no terminal)

A new **"Review"** tab in the dashboard (the admin already lives there; the control
plane's whole point was no-terminal). For each sampled overview it shows, side by
side:
- **The overview text.**
- **The source facts** — the entity's other active `knowledge_items` (publications,
  research, education, titles, bio) = exactly what the LLM was grounded on — plus a
  link to the live profile for a deeper check.
  **Join on `json_extract(metadata,'$.entity_id')`, NOT `source_url`.** `source_url`
  is unreliable as the entity key — web-merged facts carry the *personal-site* URL
  (`web_merge.py:62`), so a `source_url` join silently drops them; `entity_id` is
  non-null on every overview and fact row and is the key `reconcile_entity` itself
  uses. Fetch siblings: `WHERE json_extract(metadata,'$.entity_id')=? AND
  is_active=1 AND type!='overview'`.
- **✓ correct / ✗ wrong** buttons + an optional note ("what's wrong").

Progress shows "n of N reviewed" for the run; the admin walks the sample.

## 4. Storage — `qa_reviews` table (generic, shared with golden-eval)

The sample is **persisted at run creation** (one row per sampled item, verdict NULL
until reviewed) so a run is stable across page reloads and "n of N" is well-defined.

**Identity is `root_id` + `version`, never the volatile `item_id`.** Regeneration
deactivates the old overview row and inserts a NEW id (same `root_id`,
`version+1`) — `reconcile.py:122-129`. Overviews already churn (live DB: many at
v4/v5, 141 superseded rows), so keying on `item_id` would orphan every prior
verdict on the first regen and break the cross-run history the loop depends on.

```
qa_reviews(
  id INTEGER PK,
  run_id TEXT,            -- groups one sampling run (timestamp)
  kind TEXT,              -- 'overview' (future: 'bot_answer')
  root_id INTEGER,        -- stable per-overview identity (resolve active id at render)
  version INTEGER,        -- which overview version this run reviewed
  content_hash TEXT,      -- hash of the reviewed overview text (detects drift)
  org_id INTEGER,
  verdict TEXT,           -- NULL (drawn, unreviewed) | 'pass' | 'fail'
  note TEXT,
  reviewer TEXT,          -- 'dashboard'
  reviewed_at TEXT)
```
The active overview to display is resolved at render time:
`WHERE root_id=? AND is_active=1`. Created with `CREATE TABLE IF NOT EXISTS` by
`local_server.py` at startup (same pattern as the `jobs` table — the bot doesn't
run the v2 `create_all`).

## 5. The iterate-until-clean loop (the core workflow)

1. Start a run → system draws 20%/dept → admin reviews each ✓/✗.
2. **Any ✗ → that's the signal:** fix the **overview generation prompt**
   (`v2/core/ingestion/overview.py`'s prompt), capturing the failure note as the
   reason.
3. **Regenerate overviews** so the fix takes effect — via a new **overview-only
   regeneration** path (below), not a full re-crawl.
4. **Re-sample a fresh 20%/dept (new run) and review again.**
5. Repeat until **two consecutive clean runs** (fresh draws, 0 ✗) — a single clean
   20% draw leaves 80% unseen, so two clean draws is the pragmatic stopping rule.
   `/api/qa/summary` shows pass rate per run **and cumulative distinct overviews
   reviewed** (coverage), both keyed by `root_id`.

**Overview-only regeneration (new, required by the loop — B3).** Re-running
`ingest_faculty.py` does a *full* re-crawl (fetch+parse+decompose+overview+embed for
every profile) — too heavy to iterate on. Add an `--overview-only` mode that, per
entity: **rebuilds the `EntityRecord` from the already-stored `knowledge_items`**
(facts are in the DB — no network, no re-parse) → re-runs `overview.generate()` →
reconciles/re-embeds just the overview. This makes the fix→regenerate→re-review loop
fast and scoped to the prompt change. (If `--overview-only` is deferred, the spec
must say regen = full refresh and accept the cost — but the loop is the whole point,
so build it.)

## 6. API (local_server `/api/qa/*`, same guards as `/api/jobs/*`)

| Method & path | Purpose |
|---|---|
| `POST /api/qa/runs` `{kind:"overview"}` | start a run: draw 20%/dept, **persist the sample** (qa_reviews rows, verdict NULL), return `run_id` |
| `GET /api/qa/runs/{run_id}` | the run's persisted items (overview text + source facts joined by `entity_id`) + recorded verdicts |
| `POST /api/qa/review` `{run_id,root_id,verdict,note}` | record one verdict |
| `GET /api/qa/summary` | per-run pass rate + cumulative coverage, keyed by `root_id` |

State-changing calls (`POST /api/qa/*`) go through the **same `_csrf_ok()` +
`_host_ok()` block** as `/api/jobs/*` in `do_POST` (`local_server.py:177-179`) — add
the qa POST routes *inside* that `/api/` CSRF block, not the non-API POST path
below it. `GET /api/qa/*` needs only the Host allowlist.

## 7. Relation to the golden-eval harness (parked)

This is the **overview-faithfulness** slice. The parked **bot-answer golden set**
("ask N questions, admin confirms the answer") is a sibling that reuses the same
`qa_reviews` table (`kind='bot_answer'`) and the same Review tab. We build overview
review now; the table + UI are designed so the answer-set drops in later with no
schema change.

## 8. Testing

- **Sampling:** 20%/dept with `ceil`, min 1; **skips a dept with 0 active
  overviews**; only `type='overview' AND is_active=1`; unit-tested on a fixture item
  set.
- **Source-fact join** uses `metadata.entity_id` and includes items whose
  `source_url` differs (the web-merge case) — unit test with a mismatched
  `source_url`.
- **`qa_reviews`** created idempotently; sample persisted at run creation (NULL
  verdicts); a verdict round-trips; **runs key on `root_id`** so a regenerated
  overview (new id, same root) keeps its history.
- **Summary math:** per-run pass rate + cumulative distinct-`root_id` coverage.
- **API:** start-run persists a ~20% sample; `GET` returns items + source facts;
  review records; summary aggregates. Integration test against a temp DB **mirroring
  `bot/tests/test_control_api.py`** (server fixture + Host/CSRF guard assertions).
- The dashboard Review tab is `node --check` + live-verified (no DOM tests); render
  the side-by-side from the `/api/qa/runs/{id}` payload (live DB), not the sql.js
  snapshot.

## 9. Scope / non-goals

**v1:** sampling (20%/dept, persisted), the Review tab, the `qa_reviews` table +
`/api/qa/*`, the per-dept pass-rate + coverage summary, the **`--overview-only`
regeneration** path, and the iterate loop (fix prompt → regenerate → re-sample to
two clean runs).
**Out:** LLM pre-screen/prioritization (advisory, later); a stronger judge model;
the bot-answer golden set (later, shares this infra); auto-triggering regeneration
(the admin starts it after a prompt fix).

## 10. Risks

- **Sampling, not exhaustive:** a clean 20% draw leaves 80% unseen — it does **not**
  guarantee every overview is perfect. It certifies the *process*, and the iterate
  loop drives *systematic* errors out via the prompt. The stopping rule is **two
  consecutive clean runs** plus surfaced cumulative coverage (§5); don't oversell it
  as per-item confidence. Sample size can rise if needed.
- **Reviewer fatigue / consistency:** bounded by 20% and the side-by-side source;
  the note field captures *why* a fail, feeding prompt fixes.
- **Overview churn is real (corrects an earlier assumption):** even at temp 0 the
  local model is not perfectly reproducible, and repeated refreshes bump versions
  (live DB already shows v4/v5 and 141 superseded overview rows). That's exactly why
  runs are keyed on `root_id`+`version` (§4) — so a regeneration doesn't orphan
  prior verdicts.
