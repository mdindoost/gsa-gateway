#!/usr/bin/env python
"""Automated accuracy judge for eval/results.jsonl (self-contained — uses the local model so
re-runs need no manual grading). Rates each ANSWERED question correct/partial/wrong; deflect
and error pass through unchanged. Writes eval/results_judged.jsonl. Driven by scripts/eval.sh.

(For higher-fidelity grading you can re-judge with a stronger model; the local judge keeps the
.sh fully self-contained and repeatable.)"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bot.services.ollama_client import OllamaClient

_SYS = (
    "You grade a university assistant's answer to a graduate student. Reply with ONE word only: "
    "CORRECT if the answer addresses the question accurately and usefully, PARTIAL if it is "
    "on-topic but incomplete or hedged, or WRONG if it is inaccurate, irrelevant, or non-responsive. "
    "Do not require perfection; judge whether it sensibly answers the question."
)


def grade_reply(raw: str) -> str:
    """Map the judge's free-text reply to correct/partial/wrong. ORDERED word-boundary checks:
    'INCORRECT'/'WRONG' first (so INCORRECT doesn't match the CORRECT substring), then PARTIAL(LY),
    then a bare CORRECT; anything else defaults to wrong."""
    u = (raw or "").upper()
    if re.search(r"\b(?:WRONG|INCORRECT)\b|\bNOT\s+CORRECT\b", u):
        return "wrong"
    if re.search(r"\bPARTIAL(?:LY)?\b", u):
        return "partial"
    if re.search(r"\bCORRECT\b", u):
        return "correct"
    return "wrong"


async def judge_record(oc, r: dict) -> str:
    """Grade ONE result record. deflect/error pass through; otherwise ask the local model
    and map its reply to correct/partial/wrong (default wrong)."""
    if r.get("class") in ("deflect", "error"):
        return r["class"]
    raw = await oc.generate(f"QUESTION: {r['q']}\n\nANSWER: {r.get('answer','')[:4000]}", _SYS)
    return grade_reply(raw)


def _load_records(src: Path) -> list[dict]:
    """Read results.jsonl defensively: SKIP a malformed line (with a warning) instead of letting one
    corrupt record — e.g. from an interrupted/overlapping eval_run — crash the whole accuracy pass."""
    recs, bad = [], 0
    for i, line in enumerate(open(src, encoding="utf-8"), 1):
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError as e:
            bad += 1
            print(f"  [warn] skipping malformed results.jsonl line {i}: {e}", file=sys.stderr, flush=True)
    if bad:
        print(f"  [warn] judged {len(recs)} records, skipped {bad} malformed line(s)", flush=True)
    return recs


async def main() -> None:
    src = REPO / "eval" / "results.jsonl"
    recs = _load_records(src)
    oc = OllamaClient()
    out = open(REPO / "eval" / "results_judged.jsonl", "w", encoding="utf-8")
    for r in recs:
        r["judge"] = await judge_record(oc, r)
        out.write(json.dumps(r) + "\n")
        out.flush()
        print(f"  {r.get('judge'):8} {r['q'][:55]}", flush=True)
    out.close()
    await oc.close()
    print("JUDGE DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
