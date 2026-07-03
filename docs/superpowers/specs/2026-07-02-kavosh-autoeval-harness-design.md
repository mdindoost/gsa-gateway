# Kavosh Auto-Eval Harness — Design Spec

**Date:** 2026-07-02
**Status:** Design — pending owner review
**Type:** Side project (isolated). Zero production code changes; no memory-system entries.

## Purpose

A self-running, DB-grounded evaluation harness that manufactures realistic traffic to
continuously stress-test Kavosh while we lack real users. It runs unattended for days/weeks
(Codex is not usage-metered), survives Codex/sandbox access failures, auto-resumes around
Codex usage windows, and produces an actionable morning triage report — not a vanity
dashboard.

**Core integrity principle:** the loop can never flatter itself. Codex *generates* questions;
a *separate, mostly-deterministic* checker grades each answer against the known source item
Codex was handed. **Codex never sees or grades Kavosh's output.**

## Locked decisions

1. **Codex** (`codex exec`, GPT-5-Codex, non-metered) is the question generator — chosen
   precisely because the working-window auto-resume machinery only makes sense for Codex.
2. **Grader is a separate deterministic checker.** LLM-judge (local Ollama/Granite) is used
   ONLY for genuinely fuzzy prose and stored as a soft, clearly-labeled signal — never the
   deterministic pass/fail.
3. **Kavosh is exercised through the FULL live pipeline** (`bot/core/message_handler.handle()`)
   so render, gate, and abstention are all under test.
4. **Results persist to a separate `autoeval.db`**, never production.
5. **`LIVE_ENABLED=0` for the harness by default** — KB/KG-grounded, zero external footprint,
   zero Brave-budget spend. Live/Brave testable via a flag for occasional focused runs (with an
   optional separate Brave key).
6. **Harness traffic is invisible to all real-usage accounting** — it must never appear as a
   user question in analytics, the dashboard, feedback stats, or conversation history.
7. **Isolation:** everything under a new top-level `autoeval/` dir + `scripts/autoeval.sh`
   launcher. No edits to `message_handler.py` or any production module. The Codex-resilience
   code is **copied** into `autoeval/` (its source worktree is transient), not imported.

## Reuse map (assemble from proven parts)

| Need | Reused from | Notes |
|---|---|---|
| Pipeline wiring | `scripts/eval_run.py` | `build_assistant(config, db, kb, rate_limiter)` → `handler = asst.message_handler`; permissive `RateLimiter`; unique synthetic `user_id` per Q |
| Entry point | `bot/core/message_handler.py:206` | `async handle(MessageRequest(user_id, text, platform))` → `MessageResponse` |
| Router metadata | `v2/core/retrieval/unified_router.py:32` | `handler.unified_router.decide(query)` (sync, side-effect-free) → `RouteDecision{family, skill, args["entity_id"], source, score, margin}` |
| Ground-truth extractors | `v2/core/retrieval/entity.py`, `skills.py` | `contact_of_person`, `title_of_person`, `research_of_person`, `metric_of_person`, `entity_card`; `resolve_org`, `people_in_org` — pure SELECTs, run on a `mode=ro` conn |
| Checker normalization | `v2/core/retrieval/faithfulness.py` | `robust_grounded(quote, passages, min_overlap)` + `_norm(s)` — markdown/whitespace/casing-safe token-set overlap (the fix for the substring bug) |
| Codex resilience | teacher-eval `gather.py` + `run_until_complete.py` | `codex exec --json --skip-git-repo-check -s read-only --output-schema …`; `detect_rate_limit`, `extract_error_message`, `decide`, `parse_reset_seconds`, self-healing loop |

## Architecture

```
ITEM SAMPLER (read-only snapshot)  →  known item + ground-truth fields + missing_fields
        ↓
QUESTION GENERATOR (Codex, 3 arms) →  {question, expected_answer_spec, arm, variant_type}
        ↓
KAVOSH (full pipeline, handle())   →  answer + captured metadata
        ↓
DETERMINISTIC CHECKER              →  result + failure_class + data_gap signal + evidence
        ↓
autoeval.db  +  TRIAGE REPORT
```

Codex occupies exactly one box (generation). It never sees Kavosh's output.

### Modules (`autoeval/`)

| Module | Job |
|---|---|
| `snapshot.py` | Per-run-window `cp gsa_gateway.db → autoeval/snapshots/snap_<hash>.db`; record hash; hand both Kavosh and sampler the frozen copy |
| `sampler.py` | Sample Person/Org/ResearchArea/prose-chunk from the snapshot (configurable mix, default Person 50 / Org 20 / Area 15 / chunk 15); extract ground-truth + `has_fields` + `missing_fields`; emit a **family-typed `item_key`** (person `nodes.key` for Person, numeric `org_id` via `resolve_org` for Org, area string for Area) so the checker's resolution test compares the right arg; coverage-aware (sweeps the whole DB over time) |
| `generator.py` | Codex 3-arm generation; validates that every Q carries a machine-checkable `expected` spec; stores raw Codex prompt+response for audit |
| `runner.py` | Drive `handle()`; separately call `unified_router.decide(q)` for `family/skill/entity_id`; capture `used_ai/is_live/is_deep/source_note`; text-match canned abstain/clarify strings; time the call |
| `checker.py` | Deterministic typed checks → `result`, `failure_class`, `data_gap` signal, `evidence`; fuzzy-prose fallback to soft LLM-judge |
| `store.py` | `autoeval.db` schema + writes |
| `report.py` | Triage report generator |
| `resilience.py` | Codex access-failure handling + working-window detect/parse/sleep/auto-resume (copied) |
| `live.py` | CLI `tail` / `status`; live status file |
| `config.py` | Arm mix, sampler mix, concurrency, staleness, `LIVE_ENABLED` flag, Brave key selection |

### Data isolation (the "not user questions" guarantee)

- **Snapshot copy:** Kavosh runs against the frozen `snap_<hash>.db`, so every analytics row
  `handle()` writes (the `questions` table lives in the KB DB, `bot/services/database.py:227`)
  lands in the **throwaway snapshot** — production `gsa_gateway.db` never sees a harness row.
  This is the primary guarantee, by construction; no watermark/delete dance needed.
  - **Both DB seams must point at the snapshot.** The retriever reads `config.database_path`
    (`assistant.py:114`) and the router reads `db.db_path` (`assistant.py:132`). The harness
    mutates the `config` module singleton (`config.database_path = snapshot`) **and** constructs
    `Database(snapshot)` *before* `build_assistant`, or retrieval silently reads production while
    analytics go to the snapshot. This deliberately diverges from `eval_run.py:65`, which points
    at production on purpose.
  - **Combined mode, no production ops DB.** Construct `Database(snapshot)` with **no**
    `ops_db_path` (combined mode, `database.py:35`) so the harness never opens a connection to
    production `gsa_gateway_ops.db`. (`handle()` doesn't write ops today, but there's no reason
    to open it.)
- **Tagged synthetic identity:** every question uses a branded synthetic `user_id`
  (`autoeval::<uuid>`, hashed as usual) — trivially excludable, never collides with real
  conversation memory.
- **No telemetry side-channels:** audit `handle()`'s write paths (e.g. `log_shadow`/route-shadow);
  confirm none write to a shared production location, else redirect/disable in the harness.
- **Verification (hard-line):** diff production analytics (row counts in `questions` and any
  usage counters) before/after a smoke run; require **zero** production rows moved. Evidence
  before any "isolated" claim.
- **No external footprint:** `LIVE_ENABLED=0` default → no Brave/njit.edu traffic, no shared
  budget spend.

### Required runtime environment (exported by `scripts/autoeval.sh`, asserted at startup)

Several flags are read at **import time** as module constants; their production defaults are the
opposite of what the harness needs. The launcher exports these **before** the Python process
imports `bot.config`, and the harness asserts them at startup (fail fast if wrong):

- **`ROUTER_V21=1`** — otherwise `unified_router` is `None` (`assistant.py:125`) and the runner's
  `handler.unified_router.decide(q)` throws `AttributeError`.
- **`ROUTER_V21_SHADOW=0`** — with shadow on (the default `1`, `config.py:184`), `handle()`
  **ignores** `decide()` and answers via the legacy path (`message_handler.py:268-276`), so our
  captured `family/skill/entity_id` would describe a decision `handle()` never acted on
  (corrupts `routing_failure` classification), **and** every question appends a harness row to
  the shared `logs/router_v21_shadow.jsonl` (`route_shadow.py:15`) — a production-location leak.
  Setting it `0` fixes fidelity and the leak at once.
- **`LIVE_ENABLED=0`** — read as a module constant (`config.py:154`) and consumed directly as
  `botcfg.LIVE_ENABLED` (`message_handler.py:813,864`), so it must be an env export set before
  import, not a runtime config attribute. Guarantees zero Brave/njit.edu footprint.
- **`ROUTER_V21_SLOT_RECOVERY=0`** — see the double-`decide()` caveat below; pinning it off makes
  the captured route deterministic so it matches what `handle()` acted on.

### Metadata capture (non-invasive)

`handle()` returns only a thin `MessageResponse` (`text`, `used_ai`, `source_note`, `is_live`,
`is_deep`, `ollama_failed`, `question_id`, `offer_live_search`) — no skill/entity-key/abstain
flag, and **`used_ai` is not an abstain signal** (KG/structured/metric/clarify answers all
return `used_ai=False`; see the checker). Rather than instrument production code (would trip the
expert-review gate and break isolation), the runner **calls `handler.unified_router.decide(query)`
separately** to capture `family / skill / args`, and derives abstain/clarify purely by matching
the response text against the canned strings (`_KB_MISS_RESPONSE` `:43`, the `_useful_abstain`
lead-in "I wasn't able to find a specific answer" `:640`, `_CLARIFY_MSG` `:58`). Latency is timed
around the `await`.

**Double-`decide()` caveat:** `handle()` calls `decide()` internally (`:264`) and the runner
calls it again. For regex/fast-path/classifier routes this is deterministic and the two agree.
On the LLM-slot-extraction path (`unified_router.py:81-95`, `generate_json`) two calls are not
guaranteed identical — so with slot recovery ON the captured route could diverge from what
`handle()` used. We pin `ROUTER_V21_SLOT_RECOVERY=0` for the fidelity guarantee; rows that still
route via slot extraction are flagged and **excluded from `routing_failure` classification**
rather than trusted. (This capture also doubles the router's LLM calls per question — accepted,
consistent with GPU-polite pacing.)

## Question generator — three arms

Codex is handed ONE source item + its ground truth and must emit, for each question, an
`expected_answer_spec` derived from that ground truth (this is what makes grading
deterministic). Generation temperature high enough for variety; every question carries a
checkable spec. Raw prompt+response stored.

- **Arm A — grounded / should-answer** (~3): answer IS in the item.
  e.g. `{question:"what's Koutis's email", arm:"answer", expected:{type:"contact",
  must_contain_field:"email", value:"ikoutis@njit.edu", item_key:"crawler/ioannis-koutis",
  skill_hint:"contact_of_person"}}`
- **Arm B — noisy variants of Arm A**: typo / bad-wording / ESL / truncation variants of each
  Arm-A question, **same** `expected` spec (only phrasing degraded). Each variant tagged with
  `variant_type` (typo | wording | esl | truncation) and linked to its Arm-A twin (`twin_q_id`).
  Fuzzer for WS1 routing + WS2 resolution.
- **Arm C — out-of-scope / should-abstain** (~2, **mandatory fixed fraction**): fabricated
  person, uncovered policy, subjective question, or a field the item genuinely lacks (`field ∈
  missing_fields`). `expected:{type:"abstain_or_clarify", item_key:…, missing_field?:…}`.
  Without this arm the harness only tests answering and would optimize Kavosh into
  over-answering, re-breaking WS4.

## Deterministic checker — the integrity core

`result` ∈ {pass, fail} (Kavosh correctness). `failure_class` set only on fail. `data_gap` is a
**separate signal**, independent of pass/fail, so a missing DB field can never inflate the
routing bug count.

### Checking mechanics by `expected.type`
- **contact / typed field:** normalized presence of the expected value in the answer, using
  `faithfulness._norm` + token-set overlap (`robust_grounded`) — never a raw substring test.
- **count / metric:** numeric match of the expected figure.
- **list** (clubs, faculty): set-overlap of expected members vs. the answer; report
  precision/recall.
- **abstain_or_clarify:** pass iff the answer **text matches a canned abstain/clarify string**
  (`_KB_MISS_RESPONSE`, the `_useful_abstain` lead-in, or `_CLARIFY_MSG`). **Do NOT use
  `used_ai==False` as an abstain proxy** — KG/structured/metric answers all return
  `used_ai=False`, so that shortcut would score a confident, fabricated KG answer as a correct
  abstain and hide the exact Arm-C leak we exist to catch. Fail (→ fabrication) iff the answer is
  **not** a canned deflection AND asserts a factual value (an email/phone/number/name) — an
  affirmative factual-assertion test, never an `used_ai` check.
- **entity resolution (family-aware):** the resolved-key check depends on the routed family/skill.
  Person-centric skills carry `args["entity_id"]` (= `nodes.key`, e.g. `crawler/ioannis-koutis`);
  **Org skills carry numeric `args["org_id"]`** (not a node key), and area skills carry `args["area"]`
  (`router.py:380/424/475/534/577/589`). The sampler emits an `item_key` of the matching type per
  family (person key for Person items, numeric `org_id` — resolved via `resolve_org` — for Org
  items), and the checker compares the family-appropriate arg. Skills carrying neither a key nor an
  id (e.g. `person_disambig` → `candidates`) are treated as non-resolving for this check.
- **prose / fuzzy** (open "what is X" with no typed check): fall back to a **local LLM-judge**
  verdict stored in separate soft columns (`llm_judge_verdict`, `llm_judge_confidence`), row
  flagged `graded_soft=1` — NEVER part of the deterministic pass/fail.

### Failure-class assignment
1. **`fabrication`** (TOP SEVERITY): Arm-C question that should have abstained but produced a
   confident factual answer; OR any Arm-A/B answer asserting a value *contradicting* ground
   truth (wrong email, invented phone). WS4-regression alarm. Always fully listed, never just
   counted.
2. **`resolution_failure`**: Arm-B (noisy) fail whose Arm-A twin **passed** (A passed + B failed
   ⇒ resolution broke on noise — wrong `entity_id`, RAG fallthrough, or dead abstain). Isolates
   WS2 fuzzy/alias. If the twin A also failed, B is not classed as resolution_failure
   (→ routing_failure).
3. **`routing_failure`**: right entity available and answer exists in DB, but wrong skill/intent
   (contact routed to `entity_card`, structured question fell through to RAG and missed). Clean
   Arm-A miss lands here. Isolates WS1/router.
4. **`data_gap`** (separate signal, NOT a Kavosh bug): `field ∈ missing_fields` AND Kavosh
   correctly abstained → `result=pass` + `data_gap=1`, routed to the data-quality report, never
   the routing bug list. If the field is missing AND Kavosh fabricated a value → `result=fail`,
   `failure_class=fabrication`, `data_gap=1`.

Every row records **evidence**: the expected value, the actual answer snippet, and which check
fired.

## Operational requirements

1. **Access resilience:** every Codex/sandbox call wrapped; a transient access failure →
   bounded backoff + retry, never crashes the run. If generation is down, the harness keeps
   grading already-generated questions / idles alive rather than dying.
2. **Codex working-window auto-resume:** on a Codex usage-limit event (`detect_rate_limit` +
   `extract_error_message` reading the structured stdout `{"type":"error",…"try again at …"}`
   event), parse the reset time (`parse_reset_seconds`, handles both `"try again at 10:06 PM"`
   and `"try again at Jun 28th, 2026 3:11 AM"`), write a `paused` status carrying the reason,
   sleep until reset + buffer, and **auto-resume** generation. Unparseable → fallback cooldown
   (default 90 min). Bounded resume cycles.
3. **Live visibility:** `scripts/autoeval.sh tail` streams recent {question, arm, Kavosh answer,
   verdict}; `scripts/autoeval.sh status` shows state (running / paused-until-T /
   GPU-throttled), progress counts, and running pass/fabrication totals — backed by a live
   status file + `autoeval.db` reads.

## autoeval.db schema

- `runs(run_id, started_at, db_snapshot_hash, config_json, codex_model, kavosh_commit,
  live_enabled)`
- `questions(q_id, run_id, item_type, item_key, arm, variant_type, twin_q_id, question_text,
  expected_json, codex_raw_ref)`
- `results(q_id, answer_text, metadata_json, result, failure_class, data_gap, evidence_json,
  latency_ms, resolved_entity_id, family, skill, used_ai, graded_soft, llm_judge_verdict,
  llm_judge_confidence)`
- `coverage(item_key, times_tested, last_tested_at)`

## Triage report (the product; per run window)

- **Headline:** total Q, pass rate, and the four class counts **separately** (fabrication first,
  in red); data-gap reported apart from Kavosh bugs.
- **Fabrication list in full** — every Arm-C leak / contradiction, verbatim, with the item.
  Zero-tolerance; all listed.
- **Top failing items** clustered by `item_key` — fix items, not symptoms.
- **Resolution failures clustered by `variant_type`** — points straight at the WS2 tuning
  surface.
- **Data-gap report, separate** — "these N nodes lack field X," routed to the crawler backlog,
  explicitly NOT counted as routing bugs.
- **Regression delta** — pass rate + fabrication count vs the previous run at the same
  `kavosh_commit`.

## Defaults

- **Concurrency:** 1 Kavosh worker (GPU-bound via Ollama), tunable; Codex generation serialized
  (single working window). GPU-polite by default — a steady few QPS for weeks beats saturating
  the box.
- **Generation granularity:** one item per Codex call (clean provenance); accept call volume
  (Codex non-metered).
- **Run model:** a self-healing wrapper started once (`nohup`), mirroring
  `run_until_complete.py`; graceful resume (completed questions skipped).
- **Arm mix:** A≈3, B = variants per A, C≈2 per item (C a mandatory fixed fraction).
- **Snapshot cadence:** fresh snapshot per run window, hash-tagged; coverage persists across
  snapshots in `autoeval.db` keyed by `item_key`.

## Non-negotiables (integrity guards)

- Codex never grades Kavosh; the checker is deterministic.
- Arm C is mandatory and a fixed fraction of every run.
- `data_gap` ≠ `routing_failure`; a missing field never inflates routing bugs or triggers router
  "fixes."
- Fabrication is top severity and always fully listed.
- Production DB is never written by the harness; Kavosh runs against a snapshot copy.
- LLM-judge verdicts are soft, separate, and never part of deterministic pass/fail.
- Harness traffic never counts as user traffic (verified by before/after analytics diff).
- GPU-polite by default.

## Build order

1. `snapshot.py` + `sampler.py` + ground-truth extractor (read-only), with `missing_fields`
   detection. Validate on 10 items by hand.
2. `store.py` schema + a trivial end-to-end path (1 hand-written question → `handle()` →
   deterministic check → row). Prove the plumbing before adding Codex.
3. `checker.py` typed cases (contact/count/list/abstain/resolution). Unit-test each with known
   pass and known fail.
4. `generator.py` — Arm A first, then Arm C, then Arm B. Validate every Q carries a checkable
   `expected` spec.
5. Failure-class assignment + A/B pairing for `resolution_failure`.
6. `report.py` triage generator.
7. `resilience.py` + `live.py` + long-run wrapper: continuous loop, coverage sweep, GPU-polite
   concurrency, Codex-window auto-resume, graceful resume.

**First smoke test:** 50 items, all three arms, one pass; then read the triage report and
confirm the four classes are assigned correctly on eyeballed cases. Turn on the 24/7 loop only
once the checker's verdicts match hand judgment on a sample.

## Verify before trusting a long run

- Hand-label 10 answers (correct / fabricated / data-gap / resolution-fail); confirm the checker
  assigns the right `failure_class` to each. Calibrate the instrument before running for weeks.
- Confirm an Arm-C fabrication is caught as `fabrication`, not passed.
- Confirm a genuine missing-field case is tagged `data_gap`, not `routing_failure`.
- Confirm a noisy variant that misresolves is `resolution_failure`, paired to its passing clean
  twin.
- Confirm the production-analytics before/after diff is zero AND no harness row landed in the
  shared `logs/router_v21_shadow.jsonl` (isolation holds; `ROUTER_V21_SHADOW=0` verified).
- Confirm the four required env exports are asserted at startup (`ROUTER_V21=1`,
  `ROUTER_V21_SHADOW=0`, `LIVE_ENABLED=0`, `ROUTER_V21_SLOT_RECOVERY=0`) and that the runner's
  captured route equals the route `handle()` acted on for a sample of non-slot-extracted rows.

## Out of scope / deferred

- Instrumenting `handle()` for exact-fidelity metadata (kept non-invasive; revisit only if the
  separate `decide()` call proves to diverge).
- Live/Brave-branch testing on by default (flag-gated; optional separate Brave key).
- Any memory-system entries or production service wiring.
