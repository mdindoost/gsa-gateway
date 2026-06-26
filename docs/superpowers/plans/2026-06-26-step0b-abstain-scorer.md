# Step 0b (part 2) — Abstain Scorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Score the abstain set — measure what fraction of genuinely-unanswerable questions the bot correctly DEFLECTS (rather than confabulates), so the confidence-gate's hallucination protection becomes measurable (spec reject-criterion #3).

**Architecture:** `eval_run.py` already classifies each answer into `kb`/`live`/`deflect` and accepts `--questions`/`--out`. So the abstain set just runs through the existing harness; the only new piece is a scorer that reads the resulting jsonl and reports the deflect-rate plus the list of "leaks" (questions that got answered instead of deflected).

**Tech Stack:** Python 3.11, pytest, stdlib json.

## Global Constraints

- **Measurement-only:** new standalone script; touches NO retrieval/generation/answer-path code. One line.
- **Reuse the existing classification:** the abstain results are produced by `eval_run.py --questions eval/abstain_questions.txt`; the scorer reads the `class` field already written — it does NOT re-classify.
- **Test command:** `python3 -m pytest <file> -q`. Tests read an in-memory list of records (no file/model needed).

---

### Task 1: Add `scripts/eval_abstain.py`

**Files:**
- Create: `scripts/eval_abstain.py`
- Test: `v2/tests/test_eval_abstain.py` (create)

**Interfaces:**
- Produces:
  - `def score_abstain(recs: list[dict]) -> dict` → `{"total", "deflected", "answered", "rate", "leaks"}` where `rate = deflected/total` (0.0 if total==0) and `leaks` is the list of `{"q","class"}` for records whose `class` is NOT `"deflect"` (i.e. `kb`/`live`/`error` = a forced answer on an unanswerable question).

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_eval_abstain.py
from scripts.eval_abstain import score_abstain


def test_all_deflect_is_perfect():
    recs = [{"q": "a", "class": "deflect"}, {"q": "b", "class": "deflect"}]
    s = score_abstain(recs)
    assert s["total"] == 2 and s["deflected"] == 2 and s["answered"] == 0
    assert s["rate"] == 1.0 and s["leaks"] == []


def test_leaks_are_listed():
    recs = [
        {"q": "a", "class": "deflect"},
        {"q": "b", "class": "kb"},      # forced answer = leak
        {"q": "c", "class": "live"},    # forced answer = leak
    ]
    s = score_abstain(recs)
    assert s["total"] == 3 and s["deflected"] == 1 and s["answered"] == 2
    assert abs(s["rate"] - 1/3) < 1e-9
    assert {"q": "b", "class": "kb"} in s["leaks"]
    assert {"q": "c", "class": "live"} in s["leaks"]


def test_empty_is_zero_not_crash():
    s = score_abstain([])
    assert s["total"] == 0 and s["rate"] == 0.0 and s["leaks"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_eval_abstain.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.eval_abstain'`.

- [ ] **Step 3: Write the scorer**

```python
# scripts/eval_abstain.py
#!/usr/bin/env python
"""Score the abstain set: what fraction of genuinely-unanswerable questions the bot correctly
DEFLECTS rather than confabulates (spec reject-criterion #3). Reads an abstain results.jsonl
produced by:  python scripts/eval_run.py --questions eval/abstain_questions.txt --out eval/abstain_results.jsonl
(run with LIVE_ENABLED=0 to measure the gate itself, not the live-search fallback).
Usage: python scripts/eval_abstain.py [eval/abstain_results.jsonl]"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def score_abstain(recs: list[dict]) -> dict:
    total = len(recs)
    leaks = [{"q": r.get("q"), "class": r.get("class")} for r in recs if r.get("class") != "deflect"]
    deflected = total - len(leaks)
    return {
        "total": total,
        "deflected": deflected,
        "answered": len(leaks),
        "rate": deflected / total if total else 0.0,
        "leaks": leaks,
    }


def _main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "eval" / "abstain_results.jsonl"
    recs = [json.loads(l) for l in open(src, encoding="utf-8")]
    s = score_abstain(recs)
    print(f"abstain-correctness: {s['deflected']}/{s['total']} deflected = {100*s['rate']:.1f}%")
    if s["leaks"]:
        print(f"LEAKS — answered an unanswerable question ({s['answered']}):")
        for lk in s["leaks"]:
            print(f"  [{lk['class']}] {lk['q']}")


if __name__ == "__main__":
    _main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_eval_abstain.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/eval_abstain.py v2/tests/test_eval_abstain.py
git commit -m "feat(eval): abstain scorer — measure correct-deflect rate on unanswerable Qs (step 0b)"
```

---

## Self-Review

**Spec coverage:** Implements spec §12 R10 / reject-criterion #3 ("define the abstain/hallucination instrument"). The abstain question content already landed (`eval/abstain_questions.txt`, committed e5710ca); the held-out slice needs no code (`eval_run.py` already takes `--questions`). This scorer is the last code piece of step 0b's measurement foundation.

**Placeholder scan:** none — full runnable code + exact commands.

**Type consistency:** `score_abstain(list[dict]) -> dict` with keys `total/deflected/answered/rate/leaks`; the test asserts exactly those keys and the `leaks` element shape `{"q","class"}`. The `_main` reader matches the jsonl shape `eval_run.py` writes (`q`, `class` fields present).

**Risk note:** the scorer treats `error` class as a leak (not a correct deflect) — intentional: a crash on an unanswerable question is not the desired "graceful decline." Reviewer should confirm this matches intent.
