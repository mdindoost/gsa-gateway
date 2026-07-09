"""Layer-2 Gate-2 regression: replay the FULL faithfulness gate over the FROZEN fixture.

Integration/slow, model-pinned (granite4:tiny-h @ temp 0). Excluded from default CI (needs Ollama +
the live KG).

SCOPE (Fable, 2026-07-08 — owner-delegated): the positive-span reframe is shipped as a PARTIAL win.
granite4:tiny-h is below the answerability-judging capability floor: it hair-splits on-topic (abstains
even when the context literally answers) and warps off-topic. Two prompt iterations hit a 13->15 ceiling.
So this test enforces the SAFETY invariants strictly and pins recovery as a regression FLOOR, not the
original full-fix target:
  - HARD: every `abstain` guardrail must still abstain (0 drift leaks), and both synthetic fabrications
    must abstain — these are the merge-blockers; NEVER weaken them.
  - FLOOR: `keep` recovery must not regress below the measured RECOVERY_FLOOR (baseline was 0/39 under
    the old negative-global prompt). The remaining ~24 keeps are model-limited and DEFERRED to the M2
    chunking + query-correction-salvage tracks (a capable-enough gate model would recover most — declined
    for now to preserve the 2-model VRAM diet).

Run: python3 -m pytest v2/tests/test_gate2_regression.py -v -m integration -s
"""
import asyncio
import json
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FROZEN = REPO / "eval/processing_debt/out/gate2_fixture_frozen.jsonl"
RECOVERY_FLOOR = 15  # measured partial-ship baseline (granite4:tiny-h); regression guard, not the target

pytestmark = pytest.mark.integration

# Synthetic MUST-ABSTAIN cases (lock the fabrication guard against future prompt loosening):
#  1) fabricated answer + on-topic context that has a real responsive span (the dangerous case)
#  2) fabricated answer + off-topic context (trivial)
SYNTHETIC = [
    ("what is the opt policy",
     "Patents produced by employees are owned by the university.",
     ["The university owns patents produced by its employees under the IP policy."]),
    ("when is spring break",
     "Spring break is the third week of March.",
     ["The library is open until midnight during finals week."]),
]


def _chunk(text):
    return types.SimpleNamespace(text=text)


async def _build_handler():
    import bot.config as botcfg
    from bot.config import config
    from bot.services.database import Database
    from bot.services.knowledge_base import KnowledgeBase
    from bot.services.moderation import RateLimiter
    from bot.core.assistant import build_assistant

    botcfg.ANSWER_GATE_ENABLED = True
    db = Database(config.database_path)
    db.connect()
    db.init_tables()
    db.migrate_rag_columns()
    kb = KnowledgeBase(data_dir=config.data_dir)
    kb.load()
    asst = await build_assistant(config, db, kb, RateLimiter(max_calls=100000, period_seconds=1))
    return asst.message_handler


async def _run_all():
    """Build the handler ONCE and replay every case in the same event loop."""
    handler = await _build_handler()
    rows = [json.loads(l) for l in open(FROZEN)]

    async def gate(q, answer, passages):
        return await handler._faithfulness_gate(q, answer, [_chunk(p) for p in passages])

    fixture = [(r, await gate(r["q"], r["answer"], r["passages"])) for r in rows]
    synth = [(q, await gate(q, ans, ps)) for (q, ans, ps) in SYNTHETIC]
    return fixture, synth


def test_gate2_regression_frozen_fixture():
    fixture, synth = asyncio.run(_run_all())

    guardrails = [(r, res) for r, res in fixture if r["expected"] == "abstain"]
    keeps = [(r, res) for r, res in fixture if r["expected"] == "keep"]

    leaked = [(r["i"], r["q"], why) for (r, (keep, why)) in guardrails if keep]
    kept = [(r["i"], r["q"]) for (r, (keep, _)) in keeps if keep]
    missed = [(r["i"], r["q"], why) for (r, (keep, why)) in keeps if not keep]
    synth_leaked = [(q, why) for (q, (keep, why)) in synth if keep]

    # Always print the breakdown (run with -s) — this is the recovery diagnostic.
    print(f"\n=== Gate-2 regression: {len(kept)}/{len(keeps)} keeps surfaced (floor >={RECOVERY_FLOOR}); "
          f"{len(guardrails) - len(leaked)}/{len(guardrails)} guardrails held ===")
    if missed:
        print(f"MISSED keeps ({len(missed)}, model-limited — deferred to M2/query-correction) [i, reason]:")
        for i, q, why in missed:
            print(f"  #{i:<3} {why:<26} {q[:48]}")
    if leaked:
        print("LEAKED guardrails (should abstain, gate answered):")
        for i, q, why in leaked:
            print(f"  #{i:<3} {q[:48]}")
    if synth_leaked:
        print(f"SYNTHETIC leaked: {synth_leaked}")

    # SAFETY invariants — merge-blockers, never weaken:
    assert not leaked, f"guardrail regressions (should abstain, kept): {[i for i, _, _ in leaked]}"
    assert not synth_leaked, f"synthetic fabrication kept: {synth_leaked}"
    # RECOVERY floor — regression guard for the accepted partial ship (not the full-fix target):
    assert len(kept) >= RECOVERY_FLOOR, \
        f"recovery regressed: {len(kept)}/{len(keeps)} keeps (floor >={RECOVERY_FLOOR}); missed={missed}"
