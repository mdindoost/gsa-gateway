#!/usr/bin/env python
"""See a question travel through the whole answer pipeline:

  1. ROUTER        — the LIVE UnifiedRouter decision (family/skill/args) when ROUTER_V21=1, with the
                     deterministic route() shown alongside for contrast (it's just one input to it)
  2. FUSED POOL    — hybrid RRF (semantic KNN + bm25) order, BEFORE reranking
  3. RERANKED      — cross-encoder order, with each chunk's stored CE relevance score
  4. FINAL TOP-5   — what the bot actually answers from (entity-diversified)
  4c. EXACT PROMPT — optional (--verbose), the system+user prompt after the prefit context fit
  5. LLM ANSWER    — optional (--answer), the full real pipeline (incl. the WS4 faithfulness gate)

Usage:
  python scripts/trace_query.py "who do I contact about a billing hold"
  python scripts/trace_query.py "how do I apply for OPT" --answer
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Load .env so EMBEDDING_MODEL/OLLAMA_MODEL match the live serving config (the X-ray must embed with
# the SAME model as the DB vectors — else active_descriptor() defaults to nomic-768 and the semantic
# stage dies with a 768-vs-1024 dimension mismatch on the post-Qwen-cutover corpus).
try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except Exception:
    pass

import bot.config as botcfg
from bot.config import config
from v2.core.database.schema import get_connection
from v2.core.retrieval.router import route, Route
from v2.core.retrieval import structured_answer
from v2.core.retrieval.retriever import V2Retriever
from v2.core.retrieval.embedder import Embedder
from v2.core.retrieval.reranker import CrossEncoderReranker

DB = config.database_path        # honor DATABASE_PATH (M6) — was a hardcoded gsa_gateway.db
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
    ap.add_argument("--no-v21", action="store_true",
                    help="skip the UnifiedRouter build (faster; shows only the deterministic route())")
    args = ap.parse_args()
    q = args.question
    conn = get_connection(DB)

    print(f"\n{B}QUERY:{X} {q}")

    emb = Embedder()
    rer = CrossEncoderReranker()

    # 1. Router — the LIVE decision. ROUTER_V21=1 → the bot routes via UnifiedRouter (the deterministic
    #    route() is only ONE input to it, alongside the LLM classifier + slot-extractor); show both.
    hdr(f"1. ROUTER  (LIVE UnifiedRouter when ROUTER_V21={botcfg.ROUTER_V21}; route() shown for contrast)")
    det = route(conn, q)
    det_s = f"skill={det.skill} args={det.args}" if det else "None (→ RAG)"
    ur = None
    if botcfg.ROUTER_V21 and not args.no_v21:
        try:
            from functools import partial
            from bot.services.intent_detector import IntentDetector
            from bot.services.ollama_client import generate_json_sync
            from bot.core.assistant import maybe_build_unified_router
            gen_json = partial(generate_json_sync, base_url=config.ollama_url, model=config.ollama_model)
            ur = maybe_build_unified_router(db_path=DB, embedder=emb,
                                            intent_detector=IntentDetector(), generate_json=gen_json)
        except Exception as e:  # noqa: BLE001 - fall back to the deterministic view
            print(f"  {DIM}(UnifiedRouter build failed: {e!r} — showing deterministic route() only){X}")
    if ur is not None:
        d = ur.decide(q)
        print(f"  → {B}{d.family}{X} skill={d.skill} args={d.args} "
              f"{DIM}(source={d.source} score={getattr(d,'score',None)}){X}")
        print(f"  {DIM}deterministic route() (one input): {det_s}{X}")
        if d.family == "KG" and d.skill:
            ans = structured_answer.format_answer(structured_answer.run(conn, Route(skill=d.skill, args=dict(d.args))))
            print(("  structured answer:\n    " + ans[:600].replace("\n", "\n    ")) if ans
                  else "  (EMPTY structured result → production DEGRADES to RAG, stages 2–4)")
    else:
        # legacy view (ROUTER_V21 off, --no-v21, or build failed)
        if det:
            print(f"  → {B}STRUCTURED{X} {det_s}")
            ans = structured_answer.format_answer(structured_answer.run(conn, det))
            print("  structured answer:\n    " + ans[:600].replace("\n", "\n    "))
        else:
            print("  → None  →  falls through to semantic RAG (stages 2–4)")

    # 2. Fused-only pool (reranker off)
    hdr("2. HYBRID FUSED POOL  (RRF of semantic KNN + bm25, BEFORE rerank)")
    show(V2Retriever(conn, emb).retrieve(q, limit=args.pool, group_by_entity=False), verbose=args.verbose)

    # 3. Reranked order + CE relevance per chunk
    hdr("3. CROSS-ENCODER RERANKED  (final order; CE = the STORED rerank score production uses)")
    reranked = V2Retriever(conn, emb, reranker=rer).retrieve(q, limit=args.pool, group_by_entity=False)
    # M3: prefer the ce_score the retriever stored during rerank (what top_relevance/the gate read),
    # not a second recomputed pass over full content — recompute only if a chunk lacks it.
    stored = [getattr(c, "ce_score", None) for c in reranked]
    ce = stored if reranked and all(s is not None for s in stored) else rer.score(q, [c.content or "" for c in reranked])
    show(reranked, ce=ce, verbose=args.verbose)
    if ce is None:
        print(f"  {DIM}(reranker unavailable — order is RRF only){X}")

    # 4. What the bot answers from
    hdr("4. FINAL TOP-5  (entity-diversified — what the bot actually answers from)")
    final = V2Retriever(conn, emb, reranker=rer).retrieve(q, limit=5)
    show(final, verbose=args.verbose)

    # 4a. TIER VERDICT (M1) — what production DOES with this pool (no network). A weak top score
    #     doesn't get answered from stage 4; it falls office → deep-fallback → live njit.edu.
    hdr("4a. TIER VERDICT  (production's post-retrieval ladder for this pool)")
    top_ce = None
    if final:
        top_ce = getattr(final[0], "ce_score", None)
        if top_ce is None:
            _s = rer.score(q, [final[0].content or ""])
            top_ce = _s[0] if _s else None
    top_str = f"{top_ce:.3f}" if isinstance(top_ce, float) else str(top_ce)
    primary_miss = (not final) or (isinstance(top_ce, float) and top_ce < botcfg.LIVE_THRESHOLD)
    print(f"  top CE={top_str} vs LIVE_THRESHOLD={botcfg.LIVE_THRESHOLD}  →  primary_miss={primary_miss}")
    if primary_miss:
        live_ready = botcfg.LIVE_ENABLED and bool(botcfg.BRAVE_API_KEY)
        print(f"  → office tier (floor {botcfg.OFFICE_THRESHOLD}) · "
              f"deep-fallback {'ON' if botcfg.RETRIEVAL_DEEP_FALLBACK else 'off'} (floor {botcfg.DEEP_FALLBACK_THRESHOLD}) · "
              f"live njit.edu {'WOULD fire' if live_ready else 'off'}")
    else:
        print(f"  {DIM}(primary pool clears the floor — answered from stage 4 above){X}")

    # 4b. WS4 Gate-1 (M2) — the deterministic pre-retrieval intent deflect (only when the gate is on).
    if botcfg.ANSWER_GATE_ENABLED:
        from v2.core.retrieval.answer_gate import gate1_intent
        g1 = gate1_intent(q)
        if g1.deflect:
            print(f"  {DIM}WS4 Gate-1: WOULD deflect (cue={g1.cue}) UNLESS a structured/KG route answers it{X}")
        else:
            print(f"  {DIM}WS4 Gate-1: pass (Gate-2 answerability still applies post-generation){X}")

    # 4c. The exact prompt the LLM receives (verbose)
    if args.verbose:
        from v2.integration.retriever_shim import V2RetrieverShim
        from bot.services.ollama_client import OllamaClient
        v1 = [V2RetrieverShim._to_v1(c) for c in final]
        # Build the prompt through the CURRENT API (num_ctx/prefit refactor removed _build_full_prompt):
        # prefit() applies the exact context-budget fit that generate_answer AND the WS4 gate see.
        oc = OllamaClient(base_url=config.ollama_url, model=config.ollama_model,
                          timeout=config.ollama_timeout)
        sysp = oc._build_system_prompt(None)
        fitted = oc.prefit(q, v1)
        usrp = oc._assemble_user(oc._build_context_block(fitted), q)
        hdr(f"4c. EXACT PROMPT SENT TO THE LLM  (system + user; prefit kept {len(fitted)}/{len(v1)} chunks)")
        print(f"{B}--- SYSTEM PROMPT ---{X}\n{sysp}\n\n{B}--- USER PROMPT ---{X}\n{usrp}")

    # 5. Optional LLM answer
    if args.answer:
        hdr("5. FINAL LLM ANSWER  (full real pipeline — incl. the WS4 gate)")
        # M2: surface the gate/deep-fallback/live reasons (they're logger.debug in the handler).
        import logging
        logging.basicConfig(level=logging.WARNING)
        logging.getLogger("bot.core.message_handler").setLevel(logging.DEBUG)
        import asyncio
        from bot.services.database import Database
        from bot.services.knowledge_base import KnowledgeBase
        from bot.services.moderation import RateLimiter
        from bot.core.message_handler import MessageRequest

        async def gen():
            db = Database(config.database_path); db.connect()
            db.init_tables(); db.migrate_rag_columns()
            kb = KnowledgeBase(data_dir=config.data_dir); kb.load()
            from bot.core.assistant import build_assistant
            asst = await build_assistant(config, db, kb, RateLimiter(max_calls=99999, period_seconds=1))
            from bot.services.database import hash_user_id
            resp = await asst.message_handler.handle(
                MessageRequest(user_id="trace", text=q, platform="telegram"))
            # scrub ONLY this trace's own analytics row (by hashed synthetic id) — never an
            # id-watermark delete, which would wipe real students' questions logged concurrently.
            db.conn.execute("DELETE FROM questions WHERE user_id_hash=?",
                            (hash_user_id("trace"),)); db.conn.commit()
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
