#!/usr/bin/env python
"""See a question travel through the whole answer pipeline:

  1. ROUTER        — deterministic structured route (officers/people/areas/…) or None→RAG
  2. FUSED POOL    — hybrid RRF (semantic KNN + bm25) order, BEFORE reranking
  3. RERANKED      — cross-encoder order, with each chunk's raw CE relevance score
  4. FINAL TOP-5   — what the bot actually answers from (entity-diversified)
  5. HEADS-UP      — immigration/billing/funding match (appended to the answer)
  6. LLM ANSWER    — optional (--answer), the full real pipeline

Usage:
  python scripts/trace_query.py "who do I contact about a billing hold"
  python scripts/trace_query.py "how do I apply for OPT" --answer
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v2.core.database.schema import get_connection
from v2.core.retrieval.router import route
from v2.core.retrieval import structured_answer
from v2.core.retrieval.retriever import V2Retriever
from v2.core.retrieval.embedder import Embedder
from v2.core.retrieval.reranker import CrossEncoderReranker
from bot.core.headsup import match_topic, headsup_line

DB = str(Path(__file__).resolve().parents[1] / "gsa_gateway.db")
B, X, DIM = "\033[1m", "\033[0m", "\033[2m"


def hdr(t: str) -> None:
    print(f"\n{B}{'─' * 72}\n{t}\n{'─' * 72}{X}")


def show(chunks, ce=None, verbose=False) -> None:
    if not chunks:
        print("  (no chunks)")
        return
    for i, c in enumerate(chunks, 1):
        sim = f" sim={c.similarity:.3f}" if c.similarity is not None else ""
        cescore = f" {B}CE={ce[i - 1]:.3f}{X}" if ce else ""
        org = (c.org_path or "").split(" > ")[-1]
        print(f"  {i:>2}. rrf={c.rrf_score:.4f}{sim}{cescore}  {B}{(c.title or '')[:44]}{X}  "
              f"{DIM}({org} · doc_id={c.item_id} · {c.type}){X}")
        body = ' '.join((c.content or '').split())
        if verbose:
            print(f"      {DIM}{body}{X}")  # full chunk text
        else:
            print(f"      {DIM}{body[:120]}{X}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--answer", action="store_true", help="also generate the real LLM answer (slower)")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="show full chunk text + the exact system/user prompt sent to the LLM")
    ap.add_argument("--pool", type=int, default=8, help="how many candidates to show")
    args = ap.parse_args()
    q = args.question
    conn = get_connection(DB)

    print(f"\n{B}QUERY:{X} {q}")

    # 1. Router
    hdr("1. ROUTER  (deterministic structured routing — tried before RAG)")
    r = route(conn, q)
    if r:
        print(f"  → {B}STRUCTURED{X} skill={r.skill}  args={r.args}")
        ans = structured_answer.format_answer(structured_answer.run(conn, r))
        print("  structured answer:\n    " + ans[:600].replace("\n", "\n    "))
        print(f"\n  {DIM}(structured routes bypass retrieval/rerank — this is the final answer){X}")
    else:
        print("  → None  →  falls through to semantic RAG (stages 2–4)")

    emb = Embedder()
    rer = CrossEncoderReranker()

    # 2. Fused-only pool (reranker off)
    hdr("2. HYBRID FUSED POOL  (RRF of semantic KNN + bm25, BEFORE rerank)")
    show(V2Retriever(conn, emb).retrieve(q, limit=args.pool, group_by_entity=False), verbose=args.verbose)

    # 3. Reranked order + CE relevance per chunk
    hdr("3. CROSS-ENCODER RERANKED  (final order; CE = raw relevance the reranker assigns)")
    reranked = V2Retriever(conn, emb, reranker=rer).retrieve(q, limit=args.pool, group_by_entity=False)
    ce = rer.score(q, [c.content or "" for c in reranked])
    show(reranked, ce=ce, verbose=args.verbose)
    if ce is None:
        print(f"  {DIM}(reranker unavailable — order is RRF only){X}")

    # 4. What the bot answers from
    hdr("4. FINAL TOP-5  (entity-diversified — what the bot actually answers from)")
    final = V2Retriever(conn, emb, reranker=rer).retrieve(q, limit=5)
    show(final, verbose=args.verbose)

    # 4b. The exact prompt the LLM receives (verbose)
    if args.verbose:
        from v2.integration.retriever_shim import V2RetrieverShim
        from bot.services.ollama_client import OllamaClient
        v1 = [V2RetrieverShim._to_v1(c) for c in final]
        sysp, usrp = OllamaClient()._build_full_prompt(q, v1)
        hdr("4c. EXACT PROMPT SENT TO THE LLM  (system + user; heads-up is appended AFTER)")
        print(f"{B}--- SYSTEM PROMPT ---{X}\n{sysp}\n\n{B}--- USER PROMPT ---{X}\n{usrp}")

    # 5. Heads-up
    hdr("5. HIGH-STAKES HEADS-UP")
    t = match_topic(q)
    if t:
        print(f"  → matched {B}{t.name}{X} → {t.office}")
        print(f"  appends: {DIM}{headsup_line(t)}{X}")
    else:
        print("  → none (no immigration/billing/funding match)")

    # 6. Optional LLM answer
    if args.answer:
        hdr("6. FINAL LLM ANSWER  (full real pipeline)")
        import asyncio
        from bot.config import config
        from bot.services.database import Database
        from bot.services.knowledge_base import KnowledgeBase
        from bot.services.moderation import RateLimiter
        from bot.core.message_handler import MessageRequest

        async def gen():
            db = Database(config.database_path); db.connect()
            db.init_tables(); db.migrate_events_columns(); db.migrate_rag_columns()
            kb = KnowledgeBase(data_dir=config.data_dir); kb.load()
            from bot.core.assistant import build_assistant
            asst = await build_assistant(config, db, kb, RateLimiter(max_calls=99999, period_seconds=1))
            wm = db.conn.execute("SELECT COALESCE(MAX(id),0) FROM questions").fetchone()[0]
            resp = await asst.message_handler.handle(
                MessageRequest(user_id="trace", text=q, platform="telegram"))
            db.conn.execute("DELETE FROM questions WHERE id>?", (wm,)); db.conn.commit()
            print("  " + (resp.text or "").replace("\n", "\n  "))
            print(f"\n  {DIM}[source_note={resp.source_note} · used_ai={resp.used_ai}]{X}")
            if asst.embedder:
                await asst.embedder.close()
            if asst.ollama:
                await asst.ollama.close()
            db.close()

        asyncio.run(gen())
    print()


if __name__ == "__main__":
    main()
