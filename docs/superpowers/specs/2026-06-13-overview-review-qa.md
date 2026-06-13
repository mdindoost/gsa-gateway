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

- **20% of each department's overviews per run**, `ceil`, **minimum 1** so small
  departments are still covered. (Today: CS 56 → 12, DS 24 → 5.)
- Random per run; each run gets a `run_id` (timestamp) so runs are tracked over
  time and a re-sample after a prompt fix is a *fresh* draw.

## 3. Review surface (dashboard — no terminal)

A new **"Review"** tab in the dashboard (the admin already lives there; the control
plane's whole point was no-terminal). For each sampled overview it shows, side by
side:
- **The overview text.**
- **The source facts** — the entity's other active `knowledge_items` (publications,
  research, education, titles, bio) = exactly what the LLM was grounded on — plus a
  link to the live `source_url` profile for a deeper check.
- **✓ correct / ✗ wrong** buttons + an optional note ("what's wrong").

Progress shows "n of N reviewed" for the run; the admin walks the sample.

## 4. Storage — `qa_reviews` table (generic, shared with golden-eval)

```
qa_reviews(
  id INTEGER PK,
  run_id TEXT,            -- groups one sampling run
  kind TEXT,              -- 'overview' (future: 'bot_answer')
  item_id INTEGER,        -- knowledge_items.id of the overview
  org_id INTEGER,
  verdict TEXT,           -- 'pass' | 'fail'
  note TEXT,
  reviewer TEXT,          -- 'dashboard'
  reviewed_at TEXT)
```
Created with `CREATE TABLE IF NOT EXISTS` by `local_server.py` at startup (same
pattern as the `jobs` table — the bot doesn't run the v2 `create_all`).

## 5. The iterate-until-clean loop (the core workflow)

1. Start a run → system draws 20%/dept → admin reviews each ✓/✗.
2. **Any ✗ → that's the signal:** fix the **overview generation prompt**
   (`v2/core/ingestion/overview.py`'s prompt), capturing the failure note as the
   reason.
3. **Regenerate overviews** (re-run the refresh with `--overview`, or a targeted
   overview-only regen) so the fix takes effect.
4. **Re-sample a fresh 20%/dept and review again.**
5. Repeat until a run is **clean (0 ✗)** → the department's overviews are certified
   for that cycle. The `qa_reviews` history shows the pass rate climbing per run.

## 6. API (local_server `/api/qa/*`, same guards as `/api/jobs/*`)

| Method & path | Purpose |
|---|---|
| `POST /api/qa/runs` `{kind:"overview"}` | start a run: draw 20%/dept, return the sample (overviews + their source facts) + `run_id` |
| `GET /api/qa/runs/{run_id}` | run progress + items + recorded verdicts |
| `POST /api/qa/review` `{run_id,item_id,verdict,note}` | record one verdict |
| `GET /api/qa/summary` | pass rate per dept per run (the quality trend) |

State-changing calls carry the `X-GSA-Dashboard` header (CSRF guard), Host
allowlist on all — identical to the jobs API.

## 7. Relation to the golden-eval harness (parked)

This is the **overview-faithfulness** slice. The parked **bot-answer golden set**
("ask N questions, admin confirms the answer") is a sibling that reuses the same
`qa_reviews` table (`kind='bot_answer'`) and the same Review tab. We build overview
review now; the table + UI are designed so the answer-set drops in later with no
schema change.

## 8. Testing

- **Sampling:** 20%/dept with `ceil`, min 1; deterministic given a seed; covers
  every department that has overviews (unit-tested on a fixture set of items).
- **`qa_reviews` schema** created idempotently; a verdict round-trips
  (insert → summary reflects it).
- **Summary math:** pass rate per dept/run computed correctly.
- **API:** start-run returns a ~20% sample with source facts attached; review
  records; summary aggregates. (Integration test against a temp DB, like the jobs
  API tests.)
- The dashboard Review tab is `node --check` + live-verified (no DOM tests).

## 9. Scope / non-goals

**v1:** sampling (20%/dept), the Review tab, the `qa_reviews` table + API, the
per-dept pass-rate summary, the iterate loop (fix prompt → regenerate → re-sample).
**Out:** LLM pre-screen/prioritization (advisory, later); a stronger judge model;
the bot-answer golden set (later, shares this infra); auto-regeneration (the admin
triggers the existing refresh after a prompt fix).

## 10. Risks

- **Sampling, not exhaustive:** 20% gives statistical confidence, not a guarantee
  every overview is perfect. Acceptable — it certifies the *process*; the iterate
  loop drives systematic errors out via the prompt. Sample size/coverage can rise
  if needed.
- **Reviewer fatigue / consistency:** bounded by 20% and the side-by-side source;
  the note field captures *why* a fail, feeding prompt fixes.
- **Regeneration churn:** re-running `--overview` is deterministic (temp 0, fixed
  seed), so unchanged inputs don't churn versions — only genuinely-changed overviews
  re-embed.
