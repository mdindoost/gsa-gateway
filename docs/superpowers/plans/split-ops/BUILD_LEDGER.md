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
| 1 | Schema split + config + retire create_all (HIGH-3) | `2026-06-28-split-ops-build1-schema-config.md` | `build-1-report.md` | 🔵 DISPATCHED (Sonnet bg, in progress) |
| 2 | Repoint subsystems to two-conn | `2026-06-28-split-ops-build2-repoint.md` (SKELETON) | `build-2-report.md` | ⬜ blocked by 1 |
| 3 | EVENT→KB derive + cross-DB writes | `2026-06-28-split-ops-build3-event-derive.md` (SKELETON) | `build-3-report.md` | ⬜ blocked by 1 |
| 4 | Dashboard /db-ops + app.js two-DB | `2026-06-28-split-ops-build4-dashboard.md` (SKELETON) | `build-4-report.md` | ⬜ blocked by 1 |
| 5 | Gated migration script + acceptance gate | `2026-06-28-split-ops-build5-migration.md` (SKELETON) | `build-5-report.md` | ⬜ blocked by 1-4 |
| — | OWNER GATE: live migration + cutover | — | — | ⬜ owner-run (own checkpoint) |

## Phase plans: Phase 1 FINAL; Phases 2-5 SKELETON
Skeletons carry file lists, contracts, test intentions, acceptance + reject-criteria mapping. Before
EACH dispatch, finalize the `«LOCK AFTER P…»` signatures against the prior phase's `build-N-report.md`
(avoids speculative code). Build dispatch stays SEQUENTIAL (shared files: local_server.py spans P2/3/4;
sequential dependency on P1 seams). Optional later parallel win: P5 migration on its own worktree once
P1/P2 freeze (flagged, not default).

## Log
- 2026-06-28: spec approved+reviewed; Phase 1 plan written; ledger created.
- 2026-06-28: Build 1 DISPATCHED (background Sonnet TDD agent). Phases 2-5 SKELETON plans drafted while it runs.
