import json, tempfile, os
from autoeval.resilience import parse_reset_seconds, write_status, read_status, sleep_until_reset

def test_parse_bare_clock():
    assert parse_reset_seconds("try again at 10:06 PM") is not None

def test_parse_unparseable_returns_none():
    assert parse_reset_seconds("some unrelated error") is None

def test_status_roundtrip():
    fd, p = tempfile.mkstemp(suffix=".json"); os.close(fd)
    write_status(p, "paused", reason="usage limit", completed=5, total=10)
    st = read_status(p)
    assert st["state"] == "paused" and st["completed"] == 5

def test_sleep_until_reset_uses_default_when_unparseable():
    slept = []
    sleep_until_reset("no reset here", default=1234, buffer=0, sleep_fn=slept.append)
    assert slept == [1234]
