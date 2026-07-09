"""Freeze the 47-case Gate-2 regression fixture: re-capture full passages + untruncated answers,
flag drift vs the diagnostic record, and write a committed replay fixture. Run as a module:
    LIVE_ENABLED=0 python3 -m eval.processing_debt.build_gate2_fixture
"""
from __future__ import annotations
import asyncio, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from bot.config import config, LIVE_THRESHOLD
import bot.config as botcfg
from bot.services.database import Database
from bot.services.knowledge_base import KnowledgeBase
from bot.services.moderation import RateLimiter

LABELED = REPO / "eval/processing_debt/out/gate2_fixture_labeled.jsonl"
DIAG = REPO / "eval/processing_debt/out/prose_gate_diag.jsonl"
OUT = REPO / "eval/processing_debt/out/gate2_fixture_frozen.jsonl"


async def main() -> int:
    labeled = [json.loads(l) for l in open(LABELED)]
    diag = {r["i"]: r for r in (json.loads(l) for l in open(DIAG))}

    # NOTE: this script only CAPTURES retrieval+compose; it does not run the gate, so no gate flag needed.
    db = Database(config.database_path); db.connect(); db.init_tables(); db.migrate_rag_columns()
    kb = KnowledgeBase(data_dir=config.data_dir); kb.load()
    rl = RateLimiter(max_calls=100000, period_seconds=1)
    from bot.core.assistant import build_assistant
    asst = await build_assistant(config, db, kb, rl)
    R = asst.retriever
    ollama = asst.ollama

    out = open(OUT, "w", encoding="utf-8")
    ndrift = 0; nerr = 0
    for r in labeled:
        i, q = r["i"], r["q"]
        # `expected` is copied from the labeled source, which is the single source of truth for the
        # keep/abstain labels (incl. controller re-adjudications) — so a regeneration reproduces them.
        rec = {"i": i, "q": q, "expected": r["expected"], "passages": [], "answer": "",
               "rank1": "", "rel": None, "drift": False}
        try:
            chunks = await R.retrieve(q)          # default corpus, same as the gated path
            rec["passages"] = [(getattr(c, "text", "") or "") for c in (chunks or [])[:8]]
            rec["rel"] = round(R.top_relevance(q, chunks, skip_unscored=True), 3) if chunks else None
            if chunks:
                c0 = chunks[0]
                rec["rank1"] = (getattr(c0, "source", None) or getattr(c0, "title", None)
                                or str(getattr(c0, "metadata", ""))[:60])[:70]
            rec["answer"] = (await ollama.generate_answer(q, chunks[:8]) if chunks else "") or ""
            old = diag.get(i, {})                 # drift oracle: did rank-1 identity change?
            rec["drift"] = bool(old) and (rec["rank1"] != old.get("rank1", rec["rank1"]))
            if rec["drift"]:
                ndrift += 1
        except Exception as e:                    # a transient failure must not abort the whole capture
            rec["capture_error"] = str(e)[:200]
            nerr += 1
            print(f"[{i}] ERROR {e}")
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[{i}] drift={rec['drift']} expected={r['expected']} rel={rec['rel']} {q[:44]}")
    out.close()
    print(f"\nwrote {OUT} ; drifted cases (re-adjudicate by hand): {ndrift} ; capture errors: {nerr}")
    if nerr:
        print("WARNING: some cases failed to capture (capture_error set) — re-run before trusting the fixture.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
