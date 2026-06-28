# Split-Ops Build Ledger (L1)

**Orchestrator-owned. Only the orchestrator (main session) writes this file.** Subagents write
ONLY their own `build-<N>-report.md` and code/tests — never this ledger, never any memory file.

- **Spec:** `docs/superpowers/specs/2026-06-28-split-ops-db-design.md` (owner-approved + senior-eng review folded).
- **Worktree:** `.claude/worktrees/split-ops-db`, branch `worktree-split-ops-db`, base main `15d7338`. Prod untouched.

## Tracking tiers
- **L0** orchestrator memory `project_split_ops_db` (cross-session, orchestrator-only writer).
- **L1** this ledger (per-phase status + report pointers; git-tracked, orchestrator-only writer).
- **L2** `build-<N>-report.md` (one per dispatched agent; that agent is its only writer).

## Phases
| # | Phase | Plan | Report | Status |
|---|-------|------|--------|--------|
| 1 | Schema split + config + retire create_all (HIGH-3) | `2026-06-28-split-ops-build1-schema-config.md` | `build-1-report.md` | ⬜ ready to dispatch |
| 2 | Repoint subsystems to two-conn | (write before dispatch) | `build-2-report.md` | ⬜ blocked by 1 |
| 3 | EVENT→KB derive + cross-DB writes | (write before dispatch) | `build-3-report.md` | ⬜ blocked by 1 |
| 4 | Dashboard /db-ops + app.js two-DB | (write before dispatch) | `build-4-report.md` | ⬜ blocked by 1 |
| 5 | Gated migration script + acceptance gate | (write before dispatch) | `build-5-report.md` | ⬜ blocked by 1-4 |
| — | OWNER GATE: live migration + cutover | — | — | ⬜ owner-run (own checkpoint) |

## Phase plans are written JUST-IN-TIME
Each phase's plan is authored right before dispatch, using the actual interfaces the prior phase
delivered (avoids speculative code). Phase 1 plan is written.

## Log
- 2026-06-28: spec written+approved+reviewed; Phase 1 plan written; ledger created. Ready to dispatch Build 1.
