#!/usr/bin/env python
"""Automated accuracy judge for eval/results.jsonl (self-contained — uses the local model so
re-runs need no manual grading). Rates each ANSWERED question correct/partial/wrong; deflect
and error pass through unchanged. Writes eval/results_judged.jsonl. Driven by scripts/eval.sh.

(For higher-fidelity grading you can re-judge with a stronger model; the local judge keeps the
.sh fully self-contained and repeatable.)"""
from __future__ import annotations

import asyncio
import json
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


async def main() -> None:
    src = REPO / "eval" / "results.jsonl"
    recs = [json.loads(l) for l in open(src, encoding="utf-8")]
    oc = OllamaClient()
    out = open(REPO / "eval" / "results_judged.jsonl", "w", encoding="utf-8")
    for r in recs:
        if r.get("class") in ("deflect", "error"):
            r["judge"] = r["class"]
        else:
            raw = await oc.generate(f"QUESTION: {r['q']}\n\nANSWER: {r.get('answer','')[:1200]}", _SYS)
            u = (raw or "").upper()
            r["judge"] = "correct" if "CORRECT" in u else "partial" if "PARTIAL" in u else "wrong"
        out.write(json.dumps(r) + "\n")
        out.flush()
        print(f"  {r.get('judge'):8} {r['q'][:55]}", flush=True)
    out.close()
    await oc.close()
    print("JUDGE DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
