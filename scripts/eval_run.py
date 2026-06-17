#!/usr/bin/env python
"""Run eval/questions.txt through the REAL bot pipeline (structured router + KB retrieval +
rerank + live njit.edu fallback + heads-up), exactly as a Telegram student would hit it.
Classifies coverage per question (kb / live / deflect) and writes eval/results.jsonl.

Reusable: edit eval/questions.txt to add/remove questions (`# <category>` headers, one
question per line). Driven by scripts/eval.sh. Each question uses a unique user_id so the
rate limiter / conversation memory don't interfere."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bot.config import config
from bot.services.database import Database
from bot.services.knowledge_base import KnowledgeBase
from bot.services.moderation import RateLimiter
from bot.core.message_handler import MessageRequest

_DEFLECT = "wasn't able to find specific information"
_LIVE = "From NJIT's website:"


def load_questions(path: str) -> list[tuple[str, str]]:
    cat, out = "uncategorized", []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#"):
            c = s.lstrip("#").strip()
            if c and not c.lower().startswith("edit freely"):
                cat = c
            continue
        out.append((cat, s))
    return out


def classify(answer: str) -> str:
    if not answer or _DEFLECT in answer:
        return "deflect"
    if answer.startswith(_LIVE):
        return "live"
    return "kb"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=str(REPO / "eval" / "questions.txt"))
    ap.add_argument("--out", default=str(REPO / "eval" / "results.jsonl"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    qs = load_questions(args.questions)
    if args.limit:
        qs = qs[: args.limit]

    db = Database(config.database_path)
    db.connect()
    db.init_tables()
    db.migrate_events_columns()
    db.migrate_rag_columns()
    kb = KnowledgeBase(data_dir=config.data_dir)
    kb.load()
    rl = RateLimiter(max_calls=100000, period_seconds=1)

    from bot.core.assistant import build_assistant
    asst = await build_assistant(config, db, kb, rl)
    handler = asst.message_handler

    out = open(args.out, "w", encoding="utf-8")
    for i, (cat, q) in enumerate(qs):
        t0 = time.time()
        try:
            r = await handler.handle(MessageRequest(user_id=f"eval-{i}", text=q, platform="telegram"))
            ans = (r.text or "").strip()
            rec = {"i": i, "cat": cat, "q": q, "answer": ans, "class": classify(ans),
                   "source": r.source_note, "secs": round(time.time() - t0, 1)}
        except Exception as e:  # noqa: BLE001
            rec = {"i": i, "cat": cat, "q": q, "error": repr(e), "class": "error",
                   "secs": round(time.time() - t0, 1)}
        out.write(json.dumps(rec) + "\n")
        out.flush()
        print(f"[{i+1}/{len(qs)}] {rec['class']:7} {rec['secs']:>4}s  {q[:55]}", flush=True)
    out.close()
    if asst.embedder:
        await asst.embedder.close()
    if asst.ollama:
        await asst.ollama.close()
    db.close()
    print("RUN DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
