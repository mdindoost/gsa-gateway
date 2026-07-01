"""The robots.txt fetch must be BOUNDED by a timeout. The stdlib RobotFileParser.read()
calls urlopen with no timeout, so a host whose /robots.txt accepts the connection but never
responds hangs the whole crawl forever (observed: a 3.5h stall on one socket). _load_robots
fetches robots.txt through the timeout'd opener and parses the text instead."""
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.web_crawler import _load_robots


class _FakeResp:
    def __init__(self, body: bytes):
        self._b = body
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read1(self, n: int) -> bytes:
        part, self._b = self._b[:n], self._b[n:]
        return part


class _FakeOpener:
    """Captures the timeout passed to open() so the test can assert the read is bounded."""
    def __init__(self, body: bytes | None = None, exc: Exception | None = None):
        self.body, self.exc, self.timeout = body, exc, "unset"

    def open(self, req, timeout=None):
        self.timeout = timeout
        if self.exc is not None:
            raise self.exc
        return _FakeResp(self.body)


def test_load_robots_parses_rules_and_passes_a_timeout():
    op = _FakeOpener(body=b"User-agent: *\nDisallow: /private\n")
    rp = _load_robots(op, "https://x.njit.edu", timeout=7)
    assert op.timeout == 7                      # BOUNDED — not the stdlib unbounded read
    assert rp.can_fetch("bot", "https://x.njit.edu/public") is True
    assert rp.can_fetch("bot", "https://x.njit.edu/private") is False


def test_load_robots_transport_error_fails_open():
    # A hang/transport error must NOT propagate; caller treats None as "allowed" (fail-open,
    # matching the prior behavior where a robots miss => crawl proceeds).
    op = _FakeOpener(exc=OSError("simulated hang -> caught"))
    assert _load_robots(op, "https://x.njit.edu", timeout=7) is None
