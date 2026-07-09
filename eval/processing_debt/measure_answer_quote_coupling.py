"""Measure token-set overlap(checker_quote, composed_answer) over the KEEP cases in the frozen
fixture, plus a synthetic grounded-but-irrelevant probe. Prints the distribution + an adopt/decline
recommendation for the optional answer<->quote coupling check (Task 5 of the gate2 precision fix).

The coupling check would close the "grounded-but-irrelevant paste" channel: robust_grounded verifies
the CHECKER's quote is in context, but not that the served ANSWER uses that quote. Adopt ONLY if the
KEEP distribution is tight with a clear floor (a non-arbitrary threshold); else DECLINE (Layer-3 guards).

    LIVE_ENABLED=0 python3 -m eval.processing_debt.measure_answer_quote_coupling
"""
from __future__ import annotations
import asyncio, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from bot.config import config
from bot.services.database import Database
from bot.services.knowledge_base import KnowledgeBase
from bot.services.moderation import RateLimiter
from v2.core.retrieval.answer_gate import gate2_prompt, parse_gate2
from v2.core.retrieval.faithfulness import _norm, robust_grounded

FROZEN = REPO / "eval/processing_debt/out/gate2_fixture_frozen.jsonl"


def overlap(quote: str, answer: str) -> float:
    q = set(_norm(quote).split()); a = set(_norm(answer).split())
    return (len(q & a) / len(q)) if q else 0.0


async def _gate2(ollama, q, passages):
    sys_p, usr_p = gate2_prompt(q, [p[:1200] for p in passages[:5]])
    raw = await ollama.generate(prompt=usr_p, system=sys_p,
                                options={"temperature": 0.0, "num_predict": 256,
                                         "num_ctx": getattr(ollama, "num_ctx", 8192)}, fmt="json")
    return parse_gate2(raw or "")


async def main() -> int:
    rows = [json.loads(l) for l in open(FROZEN)]
    keeps = [r for r in rows if r["expected"] == "keep"]
    db = Database(config.database_path); db.connect(); db.init_tables(); db.migrate_rag_columns()
    kb = KnowledgeBase(data_dir=config.data_dir); kb.load()
    rl = RateLimiter(max_calls=100000, period_seconds=1)
    from bot.core.assistant import build_assistant
    asst = await build_assistant(config, db, kb, rl)
    ollama = asst.ollama

    scores = []
    for r in keeps:
        try:
            v = await _gate2(ollama, r["q"], r["passages"])
        except Exception as e:
            print(f"[{r['i']}] gate2 error {e}"); continue
        if v.quote and robust_grounded(v.quote, r["passages"]):
            s = overlap(v.quote, r["answer"])
            scores.append((r["i"], s))
            print(f"[{r['i']}] overlap={s:.2f}  {r['q'][:44]}")
    scores.sort(key=lambda t: t[1])
    vals = [s for _, s in scores]
    n = len(vals)
    print("\n" + "=" * 60)
    print(f"KEEP answer<->quote coupling over {n}/{len(keeps)} grounded cases:")
    if n:
        p10 = vals[max(0, n // 10)]
        print(f"  min={vals[0]:.2f}  p10={p10:.2f}  median={vals[n//2]:.2f}  max={vals[-1]:.2f}")
        below = [(i, s) for i, s in scores if s < 0.30]
        print(f"  cases < 0.30 overlap: {len(below)}  {below}")
        print("\nRECOMMENDATION:")
        print("  ADOPT a coupling floor (~p10) ONLY IF min is comfortably > 0 and few cases fall low.")
        print("  If several genuine keeps score low, DECLINE the check (Layer-3 remains the guard) and")
        print("  record it as an accepted-and-measured gap.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
