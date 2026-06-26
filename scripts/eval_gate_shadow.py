#!/usr/bin/env python
"""SHADOW-measure the hybrid answer-gate (spec §13.6) over the frozen instrument — READ-ONLY.

For each question: Gate 1 (deterministic intent) -> gate-the-gate on the matched-chunk ce_score
-> Gate 2 (LLM evidence-first answerability, only in the ambiguous band). It LOGS what the gate
WOULD decide; it changes no production answer and is never wired into the live path here.

Outcomes per question:
  deflect   — Gate-1 hit (terminal; skip fallback) or (not used by Gate-2 — NOT_IN_CONTEXT is fallback)
  fallback  — Gate-2 NOT_IN_CONTEXT (would go deep-fallback -> live -> deflect-only-if-miss, fold #5)
  answer    — confident retrieval (ce>=band) or Gate-2 FULLY/PARTIALLY_SUPPORTED

Scoring (2x2, spec fold #6):
  abstain set  -> CAUGHT = deflect|fallback (not answered);  LEAK = answer
  real set     -> ANSWERED ok;  fallback = soft (downstream answers);  deflect = FALSE-DEFLECT (hard-line breach)

Run with LIVE_ENABLED=0 to measure the gate, not the live fallback:
  LIVE_ENABLED=0 python scripts/eval_gate_shadow.py --band 0.70
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from v2.core.retrieval.answer_gate import gate1_intent, gate2_prompt, parse_gate2, gate_decision

DEFAULT_BAND = 0.70
TOPK_CONTEXT = 6           # chunks fed to Gate 2 (mirror what the generator sees)
CTX_CHARS = 1200           # per-chunk char cap for the Gate-2 prompt


async def _maybe_await(v):
    return await v if inspect.isawaitable(v) else v


async def shadow_one(question: str, retrieve_fn, llm_fn, band: float = DEFAULT_BAND) -> dict:
    """Decide the shadow outcome for one question. retrieve_fn(q)->(context:list[str], ce:float|None);
    llm_fn(system,user)->str. Both may be sync or async (so tests inject plain lambdas)."""
    g1 = gate1_intent(question)
    if g1.deflect:
        return {"q": question, "outcome": "deflect", "gate": "gate1", "cue": g1.cue, "ce": None}

    context, ce = await _maybe_await(retrieve_fn(question))
    dec = gate_decision(g1.cue, ce, None, band)
    if not dec.run_gate2:
        return {"q": question, "outcome": "answer", "gate": "ce_high", "ce": ce}

    system, user = gate2_prompt(question, context)
    raw = await _maybe_await(llm_fn(system, user))
    v = parse_gate2(raw)
    dec2 = gate_decision(g1.cue, ce, v.label, band)
    return {"q": question, "outcome": dec2.outcome, "gate": "gate2", "ce": ce,
            "label": v.label, "quote": (v.quote or "")[:160]}


def _load(path: str) -> list[str]:
    return [s.strip() for s in Path(path).read_text(encoding="utf-8").splitlines()
            if s.strip() and not s.startswith("#")]


def _summarize(recs: list[dict], want: str) -> dict:
    """want='deflect' (abstain set: caught=deflect|fallback) or 'answer' (real set)."""
    n = len(recs)
    answered = sum(1 for r in recs if r["outcome"] == "answer")
    fallback = sum(1 for r in recs if r["outcome"] == "fallback")
    deflected = sum(1 for r in recs if r["outcome"] == "deflect")
    if want == "deflect":
        caught = deflected + fallback
        return {"n": n, "caught": caught, "leak": answered, "rate": caught / n if n else 0.0,
                "deflect": deflected, "fallback": fallback}
    return {"n": n, "answered": answered, "fallback": fallback, "false_deflect": deflected,
            "fd_rate": deflected / n if n else 0.0}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--abstain", default=str(REPO / "eval" / "abstain_questions.txt"))
    ap.add_argument("--real", default=str(REPO / "eval" / "questions.txt"))
    ap.add_argument("--fp-traps", default=str(REPO / "eval" / "gate_fp_traps.txt"))
    ap.add_argument("--heldout", default=str(REPO / "eval" / "heldout_questions.txt"))
    ap.add_argument("--band", type=float, default=DEFAULT_BAND)
    ap.add_argument("--out", default=str(REPO / "eval" / "gate_shadow.jsonl"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    os.environ.setdefault("LIVE_ENABLED", "0")  # measure the gate, not live fallback

    from bot.config import config
    from bot.services.database import Database
    from bot.services.knowledge_base import KnowledgeBase
    from bot.services.moderation import RateLimiter
    from bot.core.assistant import build_assistant

    db = Database(config.database_path); db.connect(); db.init_tables()
    db.migrate_events_columns(); db.migrate_rag_columns()
    kb = KnowledgeBase(data_dir=config.data_dir); kb.load()
    rl = RateLimiter(max_calls=10**9, period_seconds=1)
    asst = await build_assistant(config, db, kb, rl)
    retriever, ollama = asst.retriever, asst.ollama

    async def retrieve_fn(q: str):
        chunks = await retriever.retrieve(q)
        ctx = [(getattr(c, "text", "") or "")[:CTX_CHARS] for c in chunks[:TOPK_CONTEXT]]
        ce = retriever.top_relevance(q, chunks) if hasattr(retriever, "top_relevance") else (
            (getattr(chunks[0], "metadata", {}) or {}).get("ce_score") if chunks else None)
        return ctx, ce

    async def llm_fn(system: str, user: str):
        return await ollama.generate(prompt=user, system=system) or ""

    sets = [
        ("abstain", _load(args.abstain), "deflect"),
        ("real", _load(args.real), "answer"),
        ("fp_traps", _load(args.fp_traps), "answer"),
        ("heldout", _load(args.heldout), "answer"),
    ]
    out = open(args.out, "w", encoding="utf-8")
    summary = {}
    for name, qs, want in sets:
        if args.limit:
            qs = qs[: args.limit]
        recs = []
        for i, q in enumerate(qs):
            try:
                rec = await shadow_one(q, retrieve_fn, llm_fn, band=args.band)
            except Exception as e:  # noqa: BLE001
                rec = {"q": q, "outcome": "error", "gate": "error", "err": repr(e)}
            rec["set"] = name
            recs.append(rec)
            out.write(json.dumps(rec) + "\n"); out.flush()
            print(f"[{name} {i+1}/{len(qs)}] {rec['outcome']:8} ce={rec.get('ce')}  {q[:55]}", flush=True)
        summary[name] = _summarize([r for r in recs if r["outcome"] != "error"], want)
    out.close()

    if asst.embedder:
        await asst.embedder.close()
    if ollama:
        await ollama.close()
    db.close()

    print("\n=== SHADOW 2x2 (band=%.2f) ===" % args.band)
    a = summary["abstain"]
    print(f"ABSTAIN  caught {a['caught']}/{a['n']} = {100*a['rate']:.1f}%  (deflect {a['deflect']} / fallback {a['fallback']}; LEAK {a['leak']})")
    for nm in ("real", "fp_traps", "heldout"):
        s = summary[nm]
        print(f"{nm.upper():9} answered {s['answered']}/{s['n']}; fallback {s['fallback']}; FALSE-DEFLECT {s['false_deflect']} = {100*s['fd_rate']:.1f}%")
    print("\ncutover bar: real false-deflect <= ~1-2pt AND common-case >= 84-2sigma (~82.2)")


if __name__ == "__main__":
    asyncio.run(main())
