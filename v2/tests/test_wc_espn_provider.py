"""EspnProvider — scoreboard fetch + 429/403 backoff circuit-breaker.

HTTP is injected (``http_get``) so these run with no network. The real provider uses
aiohttp; the logic under test is URL building, parse-to-NormMatch, and the block-aware
backoff that protects an unofficial endpoint with no documented rate limit.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.integration.wc_providers.espn import EspnProvider, BlockedError

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "espn_scoreboard_2026-06-24.json").read_text())


def run(coro):
    # a fresh loop per call — avoids the deprecated shared get_event_loop() that other async
    # tests may have closed (the circuit-breaker uses time.monotonic, so it's loop-independent).
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FakeHttp:
    """Records URLs; returns queued (status, payload) responses."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    async def __call__(self, url):
        self.urls.append(url)
        return self.responses.pop(0)


def test_fetch_matches_parses_scoreboard():
    http = FakeHttp([(200, FIXTURE)])
    matches = run(EspnProvider(http_get=http).fetch_matches())
    assert len(matches) == 6
    assert any(m.id == 760462 and m.state == "done" for m in matches)


def test_fetch_matches_hits_fifa_world_scoreboard_url():
    http = FakeHttp([(200, FIXTURE)])
    run(EspnProvider(http_get=http).fetch_matches())
    assert "soccer/fifa.world/scoreboard" in http.urls[0]


def test_fetch_matches_with_date_passes_dates_param():
    http = FakeHttp([(200, FIXTURE)])
    run(EspnProvider(http_get=http).fetch_matches(et_day="2026-06-24"))
    assert "dates=20260624" in http.urls[0]


def test_non_200_returns_empty_not_raise():
    http = FakeHttp([(500, None)])
    assert run(EspnProvider(http_get=http).fetch_matches()) == []


def test_429_raises_blocked_and_opens_circuit():
    prov = EspnProvider(http_get=FakeHttp([(429, None)]))
    try:
        run(prov.fetch_matches())
        assert False, "expected BlockedError"
    except BlockedError:
        pass
    assert prov.is_blocked() is True


def test_circuit_breaker_skips_calls_while_blocked():
    http = FakeHttp([(429, None)])
    prov = EspnProvider(http_get=http)
    try:
        run(prov.fetch_matches())
    except BlockedError:
        pass
    # while the circuit is open the provider returns [] WITHOUT another HTTP call
    assert run(prov.fetch_matches()) == []
    assert len(http.urls) == 1            # no second request issued


def test_backoff_grows_then_caps():
    prov = EspnProvider(http_get=FakeHttp([]))
    delays = [prov._next_backoff() for _ in range(8)]
    assert delays[0] < delays[1] < delays[2]           # exponential growth
    assert max(delays) <= prov.MAX_BACKOFF             # capped
