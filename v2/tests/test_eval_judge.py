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
