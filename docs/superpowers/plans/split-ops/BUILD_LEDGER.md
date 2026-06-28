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
| 1 | Schema split + config + retire create_all (HIGH-3) | `2026-06-28-split-ops-build1-schema-config.md` | `build-1-report.md` | ✅ DONE + reviewed (gate clean) |
| 2 | Repoint subsystems to two-conn | `2026-06-28-split-ops-build2-repoint.md` (FINAL) | `build-2-report.md` + `build-2-review-findings.md` + `build-2-fix-report.md` | ✅ DONE — built (6306c83) + dual-review CHANGES-REQUIRED + fix (6563686) + orchestrator F6 cleanup (4ffb064); GATE CLEAR (0 net-new, judging 99/99) |
| 3 | EVENT→KB derive + cross-DB writes | `2026-06-28-split-ops-build3-event-derive.md` (SKELETON) | `build-3-report.md` | ⬜ blocked by 1 |
| 4 | Dashboard /db-ops + app.js two-DB | `2026-06-28-split-ops-build4-dashboard.md` (SKELETON) | `build-4-report.md` | ⬜ blocked by 1 |
| 5 | Gated migration script + acceptance gate | `2026-06-28-split-ops-build5-migration.md` (SKELETON) | `build-5-report.md` | ⬜ blocked by 1-4 |
| — | OWNER GATE: live migration + cutover | — | — | ⬜ owner-run (own checkpoint) |

## Review strategy (dual-model at gates)
Orchestrator reviews every diff first (plan + invariants + run suites). CROSS-MODEL second opinion,
cost-tiered: critical phases — **Build 2 (repoint)** and **Build 5 (migration)** — get BOTH a Claude
senior-eng reviewer (bg general-purpose agent) AND a **Codex** review (`codex exec` / `codex exec review`,
run from the worktree on the phase diff). Light phases get one. Findings folded before the gate clears.
Live cutover gets both models too. (Codex CLI confirmed on PATH, v0.142.3.)

## Phase plans: Phase 1 FINAL; Phases 2-5 SKELETON
Skeletons carry file lists, contracts, test intentions, acceptance + reject-criteria mapping. Before
EACH dispatch, finalize the `«LOCK AFTER P…»` signatures against the prior phase's `build-N-report.md`
(avoids speculative code). Build dispatch stays SEQUENTIAL (shared files: local_server.py spans P2/3/4;
sequential dependency on P1 seams). Optional later parallel win: P5 migration on its own worktree once
P1/P2 freeze (flagged, not default).

## Log
- 2026-06-28: spec approved+reviewed; Phase 1 plan written; ledger created.
- 2026-06-28: Build 1 DISPATCHED (background Sonnet TDD agent). Phases 2-5 SKELETON plans drafted while it runs.
- 2026-06-28: Build 2 GATE CLEARED. Fix agent (6563686) fixed F1-F8; orchestrator verified (0 net-new v2+bot
  in-location, judging 99/99) + removed a cosmetic per-tick OrgCache and DEFERRED the publisher slug-resolve to
  the rebuild project (see build-2-review-findings RESOLUTION; 4ffb064). Build 2 DONE. Next: finalize+dispatch Build 3.
- 2026-06-28: Build 2 BUILT (6306c83/1addd08); test-clean (0 net-new vs base, in-location diff; judging 99/99).
  DUAL REVIEW (Claude SE agent + Codex unsandboxed) both CHANGES-REQUIRED, strong convergence. Consolidated
  + orchestrator-verified findings → `build-2-review-findings.md` (F1-F8). Core repoint correct; gaps:
  F1 migrate_events_columns crash (5 callers), F2 judging on KB (run_telegram + local_server), F3 failure-digest
  old SourceRunner sig, F4 materializers miss org_slug, F5 bot events CRUD on KB (live food path), F6 resolve_org/
  OrgCache DEAD CODE, F7 watcher conn leak, F8 weak test. Dual review PAID OFF (tests passed but real gaps). Fix pass next.
- 2026-06-28: Build 1 DONE (commits 3343a95/d7f9e1e/6b974c4/64f0e9c). Orchestrator review: caught the
  agent mislabeling a NET-NEW failure as "pre-existing/flap" — `test_events_table_is_strict` passed on
  base, broke on branch (OPS events intentionally non-STRICT, HIGH-2). Fixed it (commit cbc6b91 → assert
  live shape). PROVEN clean gate: full v2 failure-set diff branch-vs-base = ZERO net-new; judging 99/99.
  Build-1 seams: create_knowledge_schema / create_ops_schema / get_ops_connection; OPS posts/events/
  post_templates carry org_slug (DEFAULT 'gsa' — Phase 2 must resolve explicitly in enqueue_post).
  LESSON: verify agent self-assessment with evidence; don't trust "pre-existing" claims.
