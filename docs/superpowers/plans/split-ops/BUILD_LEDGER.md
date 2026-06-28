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
| 3 | EVENT→KB derive + cross-DB writes | `…build3-event-derive.md` + `build-3-review-findings.md` + `build-3-fix-report.md` | ✅ DONE — built (d37df05) + Codex CHANGES-REQUIRED + fix (6242e71/fd59671/0c4703b) + verified; GATE CLEAR (0 net-new, 28 derive tests, GSA-only, ki_content preserved). ⚠ Phase-5 back-fill flags folded into Build-5 plan. |
| 4 | Dashboard /db-ops + app.js two-DB | `2026-06-28-split-ops-build4-dashboard.md` (FINAL) | `build-4-report.md` | ✅ DONE — built (037fc39/572907d) + orchestrator review; GATE CLEAR (0 net-new vs 117-line baseline, 5 new tests pass, judging 99/99, app.js node --check OK). Single review (light phase). Browser smoke deferred to cutover (no live OPS data until Build 5). |
| 5 | Gated migration script + acceptance gate | `2026-06-28-split-ops-build5-migration.md` (FINAL) | `build-5-report.md` + `build-5-review-findings.md` + `build-5-fix-report.md` | ✅ DONE — built (6fdd5df/76070c8) + DUAL review (Claude SE + Codex) CHANGES-REQUIRED (F1-F9) + fix (588e1d0/03ada33) + Codex re-check APPROVE; GATE CLEAR. E2E proof on WAL-aware copy of live: immortal posts/post_deliveries/events/post_templates checksums MATCH backup, KB has 0 of 11 after, rollback reverts; 44 migration tests, 0 net-new, judging 99/99. |
| — | OWNER GATE: live migration + cutover | — | — | ✅ **DONE + LIVE 2026-06-28** (owner-authorized; Claude ran it). main `ed335e8`; OPS posts 431/deliv 1180/events 2/templates 2; KB 0 of 11; immortal checksums byte-identical; dashboard two-DB live; logs clean. Backup `.backups/gsa_gateway.20260628-193745-118501.pre-split-ops-migrate.db`. main NOT pushed to origin (prod=local). |

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
- 2026-06-28: Build 5 GATE CLEARED (CRITICAL phase, dual-review + Codex re-check). Plan finalized vs live
  DB + schema seams (ca7d06a): 11 MOVED tables, explicit column-map, checksum over common cols, hardened_backup,
  fail-closed gate, drop-LAST, Phase-5 back-fills. Live ground truth: posts 431 / post_deliveries 1180 /
  events 2 / post_templates 2; judging + event_info = 0 (back-fills no-op on current data). Sonnet built
  (6fdd5df) → DUAL review (Claude SE opus + Codex) both CHANGES-REQUIRED, strong convergence → consolidated
  F1-F9 (build-5-review-findings.md, 28a9a52): F1 runbook stop-services MANDATORY (report said "optional",
  used `restart.sh --no-llm` which RESTARTS the bot — wrong); F2 gate→drop window defense (busy_timeout +
  pre-drop re-verify + loud banner); F3 natural_key/ki_content backfill from MATCHED OPS event (not KB
  title/metadata — time-default divergence would dup event_info); F4 assert OPS greenfield; F5 slug gate
  asserts org_slug==map[org_id] (was non-empty only); F6-F9 #7-doc / reversibility-checksum /
  main()-fail-closed-test / rollback-recipe. Fix agent (588e1d0/03ada33) folded all 9 → Codex re-check
  APPROVE (no new regression; drop strictly after gate+reverify; common-cols checksum no false-trip; F4
  before copy; F5 fails closed). Orchestrator independently verified: WAL-aware (reads 1180 vs live),
  dry-run writes nothing, 44 migration tests, 0 net-new (117=117), judging 99/99, and a FULL E2E --commit
  proof on a WAL-aware copy of live — immortal posts/post_deliveries/events/post_templates checksums MATCH
  the pre-migration backup, KB has 0 of the 11 after, knowledge tables intact (ki 22699 / nodes 2465),
  rollback (restore + rm OPS) reverts. Build 5 DONE. Next: #8 OWNER-RUN live cutover — NOT run by the agent.
- 2026-06-28: Build 4 GATE CLEARED. Plan finalized vs live anchors (d1ba963): caught that `events` is in OPS
  too (not just posts/post_deliveries), the 3 server-load sites, the 2 post-detail fns, the org-name-on-KB
  nuance (no cross-DB JOIN), and the file-mode write caveat; documented the pre-existing 6 `test_local_server`
  403s (host-guard predates harness — NOT net-new). Sonnet TDD agent built (037fc39): `/db-ops` +
  `_send_db_ops_snapshot`; `dbOps` global + queryOps/oneOps/scalarOps (null-guarded for file-mode); loaded at
  reloadFromServer/connectToServer/reloadDbQuietly; repointed all OPS reads (renderOverview events+posts,
  openPost, renderPostsList, renderPostDetail, renderAnalytics) — org-name + renderSignature stay on `db`.
  `prepareForDashboard` correctly NOT run on dbOps (KB-only FTS). Orchestrator independently verified:
  in-location full-suite name-set diff vs pre-build baseline (117 lines) = ZERO net-new both directions;
  +5 passed (new tests); judging 99/99; app.js node --check OK. report=build-4-report.md (572907d). Next:
  finalize+dispatch Build 5 (gated migration — CRITICAL, dual Claude+Codex review) incl. folded Phase-5
  back-fills (OPS events.ki_content ← event_info.content; recompute natural_key name|date|time).
- 2026-06-28: spec approved+reviewed; Phase 1 plan written; ledger created.
- 2026-06-28: Build 1 DISPATCHED (background Sonnet TDD agent). Phases 2-5 SKELETON plans drafted while it runs.
- 2026-06-28: Build 3 GATE CLEARED. Built (d37df05) → Codex single-review CHANGES-REQUIRED (caught B3-1 HIGH
  ki_content content-loss that orchestrator's own review missed — dual/independent review earns its keep again)
  → owner decided B3-2 = GSA-only everywhere → fix (6242e71/fd59671/0c4703b) → verified (0 net-new, 28 derive
  tests, GSA gate, ki_content no-clobber guard correct, nat-key+time). Build 3 DONE. ⚠ Phase-5 must back-fill
  OPS events.ki_content from existing event_info.content + recompute natural_key (name|date|time) on existing
  event_info rows — FOLDED into Build-5 plan. Next: HOLD before Build 4 (owner asked to be looped).
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
