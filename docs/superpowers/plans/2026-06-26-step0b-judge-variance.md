# Step 0b (part 1) — Judge-Variance Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quantify the local auto-judge's run-to-run variance (σ on the aggregate correct%, plus per-question verdict-flip rate) so future eval A/B claims must exceed judge noise to count.

**Architecture:** The auto-judge (`scripts/eval_judge.py`) grades each answer with the local Ollama model (non-deterministic). Extract its single-record judging into a reusable function, then add a standalone harness that re-judges an existing `eval/results.jsonl` N times and reports the distribution of the aggregate correct% and how often individual verdicts flip.

**Tech Stack:** Python 3.11, Ollama local model, pytest, stdlib `statistics`.

## Global Constraints

- **Measurement-only, no answer-path change:** this touches ONLY the eval grading scripts; it does NOT modify retrieval, generation, or any production behavior. One line.
- **Reuse, don't duplicate (DRY):** the harness must call the SAME judging logic `eval_judge.py` uses — extract a shared function, do not copy the prompt/parse.
- **LLM-agnostic:** the judge model is whatever `OllamaClient` is configured with; no model-id baked into the new code.
- **Test command:** `python3 -m pytest <file> -q`. Tests use a STUB judge (no live model call) so they are deterministic and fast.

---

### Task 1: Extract a reusable `judge_record` function in `eval_judge.py`

**Files:**
- Modify: `scripts/eval_judge.py` (extract the per-record judging into a function `main` then calls)
- Test: `v2/tests/test_eval_judge.py` (create)

**Interfaces:**
- Produces: `async def judge_record(oc, r: dict) -> str` — returns one of `"correct" | "partial" | "wrong" | "deflect" | "error"` for a single result record `r` (the deflect/error classes pass through unchanged; otherwise it calls `oc.generate(...)` and maps the reply). `oc` is an object with `async generate(prompt, system) -> str`. Consumed by Task 2.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_eval_judge.py
import asyncio
from scripts.eval_judge import judge_record


class _StubOC:
    def __init__(self, reply): self._reply = reply
    async def generate(self, prompt, system): return self._reply


def _run(coro): return asyncio.get_event_loop().run_until_complete(coro)


def test_judge_record_passes_through_deflect_and_error():
    assert _run(judge_record(_StubOC("ignored"), {"class": "deflect", "q": "x", "answer": ""})) == "deflect"
    assert _run(judge_record(_StubOC("ignored"), {"class": "error", "q": "x", "answer": ""})) == "error"


def test_judge_record_maps_model_reply():
    r = {"class": "kb", "q": "q", "answer": "a"}
    assert _run(judge_record(_StubOC("CORRECT"), r)) == "correct"
    assert _run(judge_record(_StubOC("PARTIAL — incomplete"), r)) == "partial"
    assert _run(judge_record(_StubOC("WRONG"), r)) == "wrong"
    assert _run(judge_record(_StubOC("garbage"), r)) == "wrong"  # default when no keyword
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_eval_judge.py -q`
Expected: FAIL — `ImportError: cannot import name 'judge_record'`.

- [ ] **Step 3: Extract the function**

In `scripts/eval_judge.py`, add (keeping the existing `_SYS` constant) a module-level function, and make `main` call it:

```python
async def judge_record(oc, r: dict) -> str:
    """Grade ONE result record. deflect/error pass through; otherwise ask the local model
    and map its one-word reply to correct/partial/wrong (default wrong)."""
    if r.get("class") in ("deflect", "error"):
        return r["class"]
    raw = await oc.generate(f"QUESTION: {r['q']}\n\nANSWER: {r.get('answer','')[:1200]}", _SYS)
    u = (raw or "").upper()
    return "correct" if "CORRECT" in u else "partial" if "PARTIAL" in u else "wrong"
```

Then replace the body of `main`'s loop so it uses the function:

```python
    for r in recs:
        r["judge"] = await judge_record(oc, r)
        out.write(json.dumps(r) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_eval_judge.py -q`
Expected: PASS (4 assertions across 2 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/eval_judge.py v2/tests/test_eval_judge.py
git commit -m "refactor(eval): extract reusable judge_record from eval_judge (step 0b)"
```

---

### Task 2: Add the `eval_judge_variance.py` harness

**Files:**
- Create: `scripts/eval_judge_variance.py`
- Test: `v2/tests/test_eval_judge_variance.py` (create)

**Interfaces:**
- Consumes: `judge_record` (Task 1).
- Produces: `async def variance_runs(recs, oc, n) -> list[float]` — returns the aggregate correct-fraction (correct / answered, where answered excludes deflect+error) for each of `n` re-judging passes; and `def summarize(fractions) -> dict` → `{"runs", "mean", "stdev", "min", "max"}` using `statistics`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_eval_judge_variance.py
import asyncio
from scripts.eval_judge_variance import variance_runs, summarize


class _CycleOC:
    """Returns replies from a fixed cycle so runs differ deterministically (simulates judge noise)."""
    def __init__(self, replies): self._replies = replies; self._i = 0
    async def generate(self, prompt, system):
        v = self._replies[self._i % len(self._replies)]; self._i += 1
        return v


def _run(coro): return asyncio.get_event_loop().run_until_complete(coro)


def test_summarize_reports_stats():
    s = summarize([0.8, 1.0, 0.6])
    assert s["runs"] == 3
    assert abs(s["mean"] - 0.8) < 1e-9
    assert s["min"] == 0.6 and s["max"] == 1.0
    assert s["stdev"] > 0


def test_variance_runs_excludes_deflect_from_denominator():
    recs = [
        {"class": "kb", "q": "a", "answer": "x"},
        {"class": "kb", "q": "b", "answer": "y"},
        {"class": "deflect", "q": "c", "answer": ""},
    ]
    # one run: both answered judged CORRECT -> 2/2 = 1.0 (deflect not counted)
    fr = _run(variance_runs(recs, _CycleOC(["CORRECT", "CORRECT"]), 1))
    assert fr == [1.0]
    # one run: one CORRECT one WRONG -> 1/2 = 0.5
    fr2 = _run(variance_runs(recs, _CycleOC(["CORRECT", "WRONG"]), 1))
    assert fr2 == [0.5]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_eval_judge_variance.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.eval_judge_variance'`.

- [ ] **Step 3: Write the harness**

```python
# scripts/eval_judge_variance.py
#!/usr/bin/env python
"""Quantify the local auto-judge's run-to-run variance so eval A/B claims must exceed judge noise.
Re-judges an existing eval/results.jsonl N times (the answers are fixed; only the judge re-runs)
and reports mean/stdev/min/max of the aggregate correct%. Usage: python scripts/eval_judge_variance.py [N]"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.eval_judge import judge_record


async def variance_runs(recs, oc, n: int) -> list[float]:
    fractions = []
    for _ in range(n):
        correct = answered = 0
        for r in recs:
            verdict = await judge_record(oc, r)
            if verdict in ("deflect", "error"):
                continue
            answered += 1
            if verdict == "correct":
                correct += 1
        fractions.append(correct / answered if answered else 0.0)
    return fractions


def summarize(fractions: list[float]) -> dict:
    return {
        "runs": len(fractions),
        "mean": statistics.fmean(fractions) if fractions else 0.0,
        "stdev": statistics.stdev(fractions) if len(fractions) > 1 else 0.0,
        "min": min(fractions) if fractions else 0.0,
        "max": max(fractions) if fractions else 0.0,
    }


async def _main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    src = REPO / "eval" / "results.jsonl"
    recs = [json.loads(l) for l in open(src, encoding="utf-8")]
    from bot.services.ollama_client import OllamaClient
    fr = await variance_runs(recs, OllamaClient(), n)
    s = summarize(fr)
    print(f"judge variance over {s['runs']} runs: "
          f"correct% mean={100*s['mean']:.1f} stdev={100*s['stdev']:.2f} "
          f"min={100*s['min']:.1f} max={100*s['max']:.1f}")
    print(f"→ a claimed A/B win should exceed ~{100*2*s['stdev']:.1f} pts (2σ) to beat judge noise.")


if __name__ == "__main__":
    asyncio.run(_main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_eval_judge_variance.py -q`
Expected: PASS (test_summarize_reports_stats + test_variance_runs_excludes_deflect_from_denominator).

- [ ] **Step 5: Commit**

```bash
git add scripts/eval_judge_variance.py v2/tests/test_eval_judge_variance.py
git commit -m "feat(eval): judge-variance harness — quantify auto-judge noise (step 0b)"
```

---

## Self-Review

**Spec coverage:** Implements spec §12 R9 / revised reject-criterion #1 ("quantify auto-judge variance; a win must exceed judge σ"). The other step-0b parts (frozen held-out slice, expanded deep/high-stakes/office question sets, abstain instrument) are DEFERRED to their own plans because they require owner-authored NJIT content (anti-fabrication line) — not buildable as decision-free code here. Flagged, not dropped.

**Placeholder scan:** none — full runnable code + exact commands.

**Type consistency:** `judge_record(oc, r) -> str` defined in Task 1, imported and used in Task 2. `variance_runs(recs, oc, n) -> list[float]` and `summarize(list[float]) -> dict` consistent between the harness and its tests. The `_CycleOC`/`_StubOC` stubs match the `async generate(prompt, system)` shape `judge_record` calls.

**Risk note:** Task 1 modifies `eval_judge.py`'s `main` loop — behavior must be identical (same verdict per record). The existing `eval.sh` flow calls `eval_judge.py` as a script; `main` still writes `results_judged.jsonl` the same way. No production/answer-path code touched.
