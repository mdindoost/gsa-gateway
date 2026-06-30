# v2/tests/test_embed_self_healing.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.retrieval.embedder import embed_with_retry


def test_embed_with_retry_first_try_no_extra_calls():
    calls = []
    def call():
        calls.append(1); return [0.1, 0.2]
    assert embed_with_retry(call, attempts=3, backoff=0) == [0.1, 0.2]
    assert len(calls) == 1  # stopped at first success


def test_embed_with_retry_none_then_success():
    seq = [None, [1.0]]
    assert embed_with_retry(lambda: seq.pop(0), attempts=3, backoff=0) == [1.0]


def test_embed_with_retry_all_none_returns_none_after_attempts():
    calls = []
    def call():
        calls.append(1); return None
    assert embed_with_retry(call, attempts=3, backoff=0) is None
    assert len(calls) == 3  # exactly `attempts` tries


def test_embed_with_retry_exception_then_success_never_raises():
    seq = [RuntimeError("conn reset"), [2.0]]
    def call():
        x = seq.pop(0)
        if isinstance(x, Exception):
            raise x
        return x
    assert embed_with_retry(call, attempts=3, backoff=0) == [2.0]


def test_embed_with_retry_exception_every_time_returns_none():
    def call():
        raise TimeoutError("down")
    assert embed_with_retry(call, attempts=2, backoff=0) is None  # no raise
