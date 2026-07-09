"""Layer-3 merge gate: false-answer rate of the PRODUCTION gate on should-abstain questions.

Runs the real message_handler pipeline (gate ON, LIVE off) over the should-abstain slice of the frozen
gate-shadow instrument (set in {abstain, fp_traps}); a question that comes back NOT abstained is a LEAK
(false-answer). Merge gate: leak rate must be <= 15% (WS4 threshold).
    LIVE_ENABLED=0 python3 -m eval.processing_debt.layer3_false_answer
"""
from __future__ import annotations
import asyncio, json, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO))
from bot.config import config
import bot.config as botcfg
from bot.services.database import Database, hash_user_id
from bot.services.knowledge_base import KnowledgeBase
from bot.services.moderation import RateLimiter
from bot.core.message_handler import MessageRequest
SHADOW = REPO / "eval/gate_shadow.jsonl"

async def main() -> int:
    rows = [json.loads(l) for l in open(SHADOW)]
    abstain = [r for r in rows if r.get("set") in ("abstain", "fp_traps")]
    botcfg.ANSWER_GATE_ENABLED = True
    db = Database(config.database_path); db.connect(); db.init_tables(); db.migrate_rag_columns()
    kb = KnowledgeBase(data_dir=config.data_dir); kb.load()
    from bot.core.assistant import build_assistant
    asst = await build_assistant(config, db, kb, RateLimiter(max_calls=100000, period_seconds=1))
    h = asst.message_handler
    leaks = []
    for i, r in enumerate(abstain):
        resp = await h.handle(MessageRequest(user_id=f"l3-{i}", text=r["q"], platform="telegram"))
        ab = getattr(resp, "is_abstain", False)
        if not ab:  # answered a should-abstain question -> false-answer leak
            leaks.append((r.get("set"), r["q"], (resp.text or "")[:80].replace("\n", " ")))
    try:
        db.conn.executemany("DELETE FROM questions WHERE user_id_hash=?",
                            [(hash_user_id(f"l3-{i}"),) for i in range(len(abstain))]); db.conn.commit()
    except Exception:
        pass
    n = len(abstain); nl = len(leaks)
    print(f"\n=== Layer-3 false-answer (production gate, should-abstain set) ===")
    print(f"  should-abstain questions: {n}  |  leaked (false-answer): {nl}  ->  {100*nl/n:.1f}%")
    print(f"  MERGE GATE (<=15%): {'PASS' if nl/n <= 0.15 else 'FAIL'}")
    for s, q, t in leaks:
        print(f"  LEAK [{s}] {q[:44]} -> {t}")
    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
