import asyncio, os, tempfile
import pytest
from autoeval.codex_client import RateLimitError
from autoeval.harness import generate_with_resume

class _Cfg:
    def __init__(self, status_file):
        self.status_file = status_file

def _cfg():
    fd, p = tempfile.mkstemp(suffix=".json"); os.close(fd)
    return _Cfg(p)

def test_generate_with_resume_retries_across_multiple_windows():
    calls = {"n": 0}
    waits = []
    async def gen(item):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RateLimitError("try again at 10:06 PM")
        return ["Q"]
    async def wait(reason):
        waits.append(reason)
    out = asyncio.run(generate_with_resume("item", _cfg(), run_id=1, completed=0, total=1,
                                           generate_fn=gen, wait_fn=wait, max_cycles=16))
    assert out == ["Q"]
    assert calls["n"] == 3 and len(waits) == 2   # waited across two windows, succeeded on the third

def test_generate_with_resume_raises_after_max_cycles():
    async def gen(item):
        raise RateLimitError("try again at 10:06 PM")
    async def wait(reason):
        pass
    with pytest.raises(RateLimitError):
        asyncio.run(generate_with_resume("item", _cfg(), run_id=1, completed=0, total=1,
                                         generate_fn=gen, wait_fn=wait, max_cycles=3))
