#!/usr/bin/env python
"""SHADOW-measure the hybrid answer-gate (spec §13.6) over the frozen instrument — READ-ONLY.

Per question: Gate 1 (deterministic intent) -> production structured/KG exemption (fold #7) ->
gate-the-gate on the matched-chunk ce_score (+ always for fact-shaped Qs, review B2) -> Gate 2 (LLM
evidence-first answerability at TEMP 0, review B1) -> quote-grounding verification (review I5). It
LOGS what the gate WOULD decide; it changes no production answer.

For a clean band-sweep (review I8), the measurement runs Gate 2 on EVERY gated question once (records
ce + verified label), then re-derives the outcome at any band post-hoc via score_at_band — so one run
yields the abstain-caught / false-deflect frontier across bands without re-calling the model.

Outcomes (per band):
  deflect   — Gate-1 hit (terminal; skip fallback)
  fallback  — Gate-2 NOT_IN_CONTEXT (-> deep-fallback -> live -> deflect-only-if-miss, fold #5)
  answer    — exempt/structured, confident retrieval (ce>=band, non-fact), or Gate-2 SUPPORTED

Scoring (2x2, fold #6):
  abstain set  -> CAUGHT = deflect|fallback;  LEAK = answer
  real/fp/heldout -> ANSWERED ok;  fallback = soft (downstream answers);  deflect = FALSE-DEFLECT (hard-line breach)

  LIVE_ENABLED=0 python scripts/eval_gate_shadow.py --band 0.70 --sweep
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

from v2.core.retrieval.answer_gate import (
    gate1_intent, gate2_prompt, parse_gate2, gate_decision, is_fact_shaped, verify_support,
)

DEFAULT_BAND = 0.70
SWEEP_BANDS = [0.30, 0.50, 0.70, 0.85]
TOPK_CONTEXT = 5           # matches V2Retriever.retrieve(limit=5) — the generator's context size
CTX_CHARS = 1200
GATE2_OPTS = {"temperature": 0.0, "num_predict": 256}   # B1: deterministic, constrained
GATE2_FMT = "json"


async def _maybe_await(v):
    return await v if inspect.isawaitable(v) else v


async def shadow_one(question: str, retrieve_fn, llm_fn, band: float = DEFAULT_BAND) -> dict:
    """Production-faithful single-band decision for one question (gate-the-gate ACTIVE). Reference
    implementation used by tests; main() uses the always-run measurement loop for the sweep."""
    g1 = gate1_intent(question)
    if g1.deflect:
        return {"q": question, "outcome": "deflect", "gate": "gate1", "cue": g1.cue, "ce": None}

    context, ce = await _maybe_await(retrieve_fn(question))
    fact = is_fact_shaped(question)
    dec = gate_decision(g1.cue, ce, None, band, fact_shaped=fact)
    if not dec.run_gate2:
        return {"q": question, "outcome": "answer", "gate": "ce_high", "ce": ce, "fact_shaped": fact}

    system, user = gate2_prompt(question, context)
    raw = await _maybe_await(llm_fn(system, user))
    v = verify_support(parse_gate2(raw), context)
    dec2 = gate_decision(g1.cue, ce, v.label, band, fact_shaped=fact)
    return {"q": question, "outcome": dec2.outcome, "gate": "gate2", "ce": ce, "label": v.label,
            "quote": (v.quote or "")[:160], "fact_shaped": fact, "parsed": v.parsed,
            "empty": not (raw or "").strip()}


# ──────────────────────────────────────────────────────── post-hoc scoring (band sweep)
def derive_outcome(rec: dict, band: float) -> str:
    """Re-derive the gate outcome for a measured record at an arbitrary band."""
    stage = rec.get("stage")
    if stage == "gate1":
        return "deflect"
    if stage == "exempt":
        return "answer"
    ce, fact = rec.get("ce"), rec.get("fact_shaped", False)
    if ce is not None and ce >= band and not fact:
        return "answer"                                   # gate-the-gate skip
    return "fallback" if rec.get("label") == "NOT_IN_CONTEXT" else "answer"


def score_at_band(recs: list[dict], band: float, want: str) -> dict:
    outs = [derive_outcome(r, band) for r in recs]
    n = len(outs)
    answered = outs.count("answer")
    fallback = outs.count("fallback")
    deflected = outs.count("deflect")
    if want == "deflect":
        return {"n": n, "caught": deflected + fallback, "leak": answered,
                "rate": (deflected + fallback) / n if n else 0.0, "deflect": deflected, "fallback": fallback}
    return {"n": n, "answered": answered, "fallback": fallback, "false_deflect": deflected,
            "fd_rate": deflected / n if n else 0.0}


def _load(path: str) -> list[tuple[str | None, str]]:
    """Returns [(class_label_or_None, question)]; class_label tracks abstain '# (a) ...' headers."""
    out, cls = [], None
    for s in Path(path).read_text(encoding="utf-8").splitlines():
        s = s.strip()
        if not s:
            continue
        if s.startswith("#"):
            head = s.lstrip("#").strip()
            if head.startswith("("):           # e.g. "(f) in-domain but NOT in corpus ..."
                cls = head.split(")")[0].lstrip("(")
            continue
        out.append((cls, s))
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--abstain", default=str(REPO / "eval" / "abstain_questions.txt"))
    ap.add_argument("--real", default=str(REPO / "eval" / "questions.txt"))
    ap.add_argument("--fp-traps", default=str(REPO / "eval" / "gate_fp_traps.txt"))
    ap.add_argument("--heldout", default=str(REPO / "eval" / "heldout_questions.txt"))
    ap.add_argument("--band", type=float, default=DEFAULT_BAND)
    ap.add_argument("--sweep", action="store_true", help="print the band frontier from the same run")
    ap.add_argument("--out", default=str(REPO / "eval" / "gate_shadow.jsonl"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    os.environ.setdefault("LIVE_ENABLED", "0")  # defensive: measure the gate, not live fallback

    from bot.config import config
    from bot.services.database import Database
    from bot.services.knowledge_base import KnowledgeBase
    from bot.services.moderation import RateLimiter
    from bot.core.assistant import build_assistant

    db = Database(config.database_path, config.operations_db_path); db.connect(); db.init_tables()
    db.migrate_events_columns(); db.migrate_rag_columns()
    kb = KnowledgeBase(data_dir=config.data_dir); kb.load()
    rl = RateLimiter(max_calls=10**9, period_seconds=1)
    asst = await build_assistant(config, db, kb, rl)
    retriever, ollama, handler = asst.retriever, asst.ollama, asst.message_handler

    async def retrieve_one(q: str):
        chunks = await retriever.retrieve(q)
        ctx = [(getattr(c, "text", "") or "")[:CTX_CHARS] for c in chunks[:TOPK_CONTEXT]]
        ce = retriever.top_relevance(q, chunks) if hasattr(retriever, "top_relevance") else (
            (getattr(chunks[0], "metadata", {}) or {}).get("ce_score") if chunks else None)
        return ctx, ce

    async def measure_one(cls: str | None, q: str) -> dict:
        """Run the full gate ONCE, always invoking Gate 2 for gated Qs so any band re-scores cleanly."""
        rec: dict = {"q": q, "cls": cls}
        g1 = gate1_intent(q)
        if g1.deflect:
            rec.update(stage="gate1", cue=g1.cue, ce=None); return rec
        structured = await handler._try_structured(q)            # fold #7: production exempts these
        if structured is not None:
            rec.update(stage="exempt", ce=None); return rec
        ctx, ce = await retrieve_one(q)
        fact = is_fact_shaped(q)
        system, user = gate2_prompt(q, ctx)
        raw = await ollama.generate(prompt=user, system=system, options=GATE2_OPTS, fmt=GATE2_FMT) or ""
        v = verify_support(parse_gate2(raw), ctx)
        rec.update(stage="gated", ce=ce, fact_shaped=fact, label=v.label, parsed=v.parsed,
                   empty=not raw.strip(), quote=(v.quote or "")[:160])
        return rec

    sets = [("abstain", _load(args.abstain), "deflect"),
            ("real", _load(args.real), "answer"),
            ("fp_traps", _load(args.fp_traps), "answer"),
            ("heldout", _load(args.heldout), "answer")]

    by_set: dict[str, list[dict]] = {}
    errors = 0
    out = open(args.out, "w", encoding="utf-8")
    try:
        for name, items, _want in sets:
            if args.limit:
                items = items[: args.limit]
            recs = []
            for i, (cls, q) in enumerate(items):
                try:
                    rec = await measure_one(cls, q)
                except Exception as e:  # noqa: BLE001
                    rec = {"q": q, "cls": cls, "stage": "error", "err": repr(e)}; errors += 1
                rec["set"] = name
                recs.append(rec)
                out.write(json.dumps(rec) + "\n"); out.flush()
                tag = rec.get("stage"); ce = rec.get("ce")
                print(f"[{name} {i+1}/{len(items)}] {tag:7} ce={ce}  {q[:50]}", flush=True)
            by_set[name] = recs
    finally:
        out.close()
        try:
            if asst.embedder: await asst.embedder.close()
            if getattr(retriever, "embedder", None) and hasattr(retriever.embedder, "close"):
                await retriever.embedder.close()
            if ollama: await ollama.close()
        finally:
            db.close()

    def report(band: float) -> None:
        ok = lambda name: [r for r in by_set[name] if r.get("stage") != "error"]
        a = score_at_band(ok("abstain"), band, "deflect")
        print(f"\n=== band {band:.2f} ===")
        print(f"ABSTAIN  caught {a['caught']}/{a['n']} = {100*a['rate']:.1f}%  (deflect {a['deflect']} / fallback {a['fallback']}; LEAK {a['leak']})")
        for nm in ("real", "fp_traps", "heldout"):
            s = score_at_band(ok(nm), band, "answer")
            print(f"{nm.upper():9} answered {s['answered']}/{s['n']}; fallback {s['fallback']}; FALSE-DEFLECT {s['false_deflect']} = {100*s['fd_rate']:.1f}%")

    print("\n========== SHADOW 2x2 ==========")
    if errors:
        print(f"!! {errors} measurement errors (excluded from rates) — investigate before trusting numbers")
    n_empty = sum(1 for r in by_set.get("real", []) + by_set.get("abstain", []) if r.get("empty"))
    if n_empty:
        print(f"!! {n_empty} Gate-2 calls returned EMPTY (answer-biased default; degraded run signal — review I2)")
    report(args.band)

    # per-class abstain breakdown (review B2 — pooled rate hides hardest classes)
    print(f"\n--- abstain by class (band {args.band:.2f}) ---")
    cls_recs: dict[str, list[dict]] = {}
    for r in by_set["abstain"]:
        if r.get("stage") != "error":
            cls_recs.setdefault(r.get("cls") or "?", []).append(r)
    for c in sorted(cls_recs):
        s = score_at_band(cls_recs[c], args.band, "deflect")
        print(f"  ({c}) caught {s['caught']}/{s['n']} = {100*s['rate']:.0f}%")

    if args.sweep:
        print("\n--- band sweep (abstain-caught % / real false-deflect %) ---")
        for b in SWEEP_BANDS:
            ab = score_at_band([r for r in by_set["abstain"] if r.get("stage") != "error"], b, "deflect")
            rl_ = score_at_band([r for r in by_set["real"] if r.get("stage") != "error"], b, "answer")
            ho = score_at_band([r for r in by_set["heldout"] if r.get("stage") != "error"], b, "answer")
            print(f"  band {b:.2f}: abstain {100*ab['rate']:.0f}%  real-FD {100*rl_['fd_rate']:.1f}%  heldout-FD {100*ho['fd_rate']:.1f}%")

    print("\ncutover bar: real false-deflect <= ~1-2pt AND common-case >= 84-2sigma (~82.2)")


if __name__ == "__main__":
    asyncio.run(main())
