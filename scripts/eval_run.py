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


def classify(answer: str, is_live: bool = False, is_abstain: bool = False) -> str:
    """Coverage class from STRUCTURED signals only (no answer-text coupling). is_live wins (the
    njit.edu fallback flag is authoritative); is_abstain (tag-at-source on every canned non-answer)
    or an empty answer → deflect; else a real KB/KG answer."""
    if is_live:
        return "live"
    if is_abstain or not answer:
        return "deflect"
    return "kb"


def cleanup_eval_rows(db, n: int) -> int:
    """Remove the analytics rows this eval created — matched by the hashed synthetic user_ids
    (eval-0 … eval-{n-1}), NEVER by an id range. Returns the rows deleted. executemany so an
    arbitrarily large n never hits SQLite's bound-variable limit."""
    from bot.services.database import hash_user_id
    hashes = [(hash_user_id(f"eval-{i}"),) for i in range(n)]
    cur = db.conn.executemany("DELETE FROM questions WHERE user_id_hash=?", hashes)
    db.conn.commit()
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=str(REPO / "eval" / "questions.txt"))
    ap.add_argument("--out", default=str(REPO / "eval" / "results.jsonl"))
    ap.add_argument("--limit", type=int, default=0)
    args, _ = ap.parse_known_args()   # tolerate eval_report's gate flags (--min-answered/--min-correct)

    qs = load_questions(args.questions)
    if args.limit:
        qs = qs[: args.limit]

    db = Database(config.database_path)
    db.connect()
    db.init_tables()
    db.migrate_rag_columns()
    kb = KnowledgeBase(data_dir=config.data_dir)
    kb.load()
    rl = RateLimiter(max_calls=100000, period_seconds=1)

    from bot.core.assistant import build_assistant
    asst = await build_assistant(config, db, kb, rl)
    handler = asst.message_handler

    out = open(args.out, "w", encoding="utf-8")
    try:
        for i, (cat, q) in enumerate(qs):
            t0 = time.time()
            try:
                r = await handler.handle(MessageRequest(user_id=f"eval-{i}", text=q, platform="telegram"))
                ans = (r.text or "").strip()
                is_live = getattr(r, "is_live", False)
                is_abstain = getattr(r, "is_abstain", False)
                rec = {"i": i, "cat": cat, "q": q, "answer": ans,
                       "class": classify(ans, is_live, is_abstain),
                       "source": r.source_note, "is_deep": getattr(r, "is_deep", False),
                       "is_live": is_live, "is_abstain": is_abstain,
                       "abstain_reason": getattr(r, "abstain_reason", None),
                       "offer": getattr(r, "offer_live_search", False),
                       "secs": round(time.time() - t0, 1)}
            except Exception as e:  # noqa: BLE001
                rec = {"i": i, "cat": cat, "q": q, "error": repr(e), "class": "error",
                       "secs": round(time.time() - t0, 1)}
            out.write(json.dumps(rec) + "\n")
            out.flush()
            print(f"[{i+1}/{len(qs)}] {rec['class']:7} {rec['secs']:>4}s  {q[:55]}", flush=True)
    finally:
        # Delete ONLY this eval's synthetic analytics rows, scoped by user_id_hash — NEVER by an
        # id-watermark (the bots run against the same live DB; a watermark delete would wipe real
        # students' questions logged during the run). In finally so a crash can't skip it, and a
        # generous range self-heals rows left by any previously-crashed eval run. [Fable C5/H3/L1]
        cleanup_eval_rows(db, n=max(len(qs), 2000))
        out.close()
        if asst.embedder:
            await asst.embedder.close()
        if asst.ollama:
            await asst.ollama.close()
        db.close()
    print("RUN DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
