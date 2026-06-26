import asyncio
from scripts.eval_judge_variance import variance_runs, summarize


class _CycleOC:
    """Returns replies from a fixed cycle so runs differ deterministically (simulates judge noise)."""
    def __init__(self, replies): self._replies = replies; self._i = 0
    async def generate(self, prompt, system):
        v = self._replies[self._i % len(self._replies)]; self._i += 1
        return v


def _run(coro): return asyncio.run(coro)


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


def test_variance_runs_multiple_runs_differ_when_judge_flips():
    recs = [{"class": "kb", "q": "a", "answer": "x"}]
    # judge says CORRECT on run 1, WRONG on run 2 (one answered record per run)
    fr = _run(variance_runs(recs, _CycleOC(["CORRECT", "WRONG"]), 2))
    assert fr == [1.0, 0.0]
    s = summarize(fr)
    assert s["runs"] == 2 and s["min"] == 0.0 and s["max"] == 1.0 and s["stdev"] > 0
