# Oracle Processing-Debt Project

> **Self-contained research + design workspace.** Everything about this project lives in THIS folder.
> Its living memory is [`PROJECT_MEMORY.md`](./PROJECT_MEMORY.md) — NOT the global `~/.claude/.../MEMORY.md`.
> When resuming: **read `PROJECT_MEMORY.md` first**, then whatever file it points you to.

## One line
Use a cheap web-grounded LLM oracle (**Brave Answers**, answer + citations) as a **diagnostic** — not a
knowledge source — to measure and fix how much of our **already-owned knowledge our pipeline fails to
surface** (routing / retrieval / rerank / compose). "Process distillation, not knowledge distillation."

## The thesis (owner's)
> We are **not** missing knowledge — we hold a near-complete authoritative corpus (KB/KG). Our weakness
> is **processing**. So this is *process* distillation: given facts we already have, learn to
> route/retrieve/rank/compose as well as a good teacher, **without importing the teacher's knowledge**.
> Because we own the facts, we can even out-ground the teacher.

## The mechanism (what makes it actionable)
For every fact in the oracle's answer that OUR answer missed, do a **second lookup** — is that fact
present *anywhere* in OUR corpus?
- **Present but not surfaced → PROCESSING gap** (fixable, zero new data) — the gold.
- **Absent → knowledge/crawl gap** — separate track.

Headline metric = **"% of facts we already own that we failed to surface" = Processing Debt**, sliced by
pipeline stage (router / pool / rerank / compose).

## Cost reality (hard constraint)
Brave Answers ≈ **$0.057/query** (~10.6k tokens/query × $5/M + $4/1k queries). **$5 free credits/month
≈ ~88 queries.** Owner budget ≈ **$30**. → Full 2000-question run (~$114) is off the table. Plan =
**~50-question pilot first (~$3–4)**, then decide whether to scale (ceiling ~450–500 Qs).

## How to use this folder
1. **Read `PROJECT_MEMORY.md`** — current state, decisions, open questions, next action.
2. Read the research + reviews it points to.
3. Update `PROJECT_MEMORY.md` as the single source of truth for this project. Do **not** log this
   project in the global memory index (owner wants main memory kept clean; owner points Claude here).

## Files in this folder
| File | What |
|---|---|
| `PROJECT_MEMORY.md` | **Living state** — read first, update always. |
| `2026-07-06-fable-review-oracle-diff-gapfinder.md` | Fable's senior review of the thesis + method (4 required fixes). |
| `2026-07-06-deep-research-*.md` | Deep-research literature report (added when the workflow completes). |
| *(future)* `…-design.md` | The design spec, once we brainstorm past research. |

## Related (elsewhere in repo — read-only inputs, not owned by this project)
- `docs/SampleQuestions/` — 2000 real messy student Qs (1000 DB-answerable + 1000 web-needing).
- `scripts/eval_*.py`, `scripts/eval.sh`, `scripts/ask.sh` — existing eval stack + pipeline X-ray.
- Global memory `project_codex_oracle_eval` / `project_autoeval_harness` — the parked predecessors this
  supersedes/complements.
