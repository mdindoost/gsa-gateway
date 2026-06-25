"""EspnProvider — scoreboard-primary World Cup fetch with a block-aware circuit-breaker.

ESPN's public endpoints publish no rate limit ("excessive requests may be blocked"), and
there's no API key to round-robin, so a single source IP polling every ~2s is the whole
risk surface. We mirror the reference client (pseudo-r/Public-ESPN-API): real User-Agent,
per-request timeout, and treat 429/403 as a hard block → open a circuit-breaker that makes
subsequent calls cheap no-ops (return []) and backs off exponentially before retrying. The
watcher degrades silently rather than hammering a throttling host.

HTTP is injected (``http_get(url) -> (status, json|None)``) so the logic is unit-testable;
the default uses aiohttp.
"""
from __future__ import annotations

import asyncio
import logging
import time

from v2.integration.wc_providers.normalize import scoreboard_to_matches

logger = logging.getLogger(__name__)

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
USER_AGENT = "GSA-Gateway/1.0 (+https://gsanjit.com; NJIT Graduate Student Association)"


class BlockedError(Exception):
    """Raised when ESPN returns 429/403 — the circuit-breaker opens."""


async def _aiohttp_get(url: str):
    import aiohttp
    async with aiohttp.ClientSession() as s:
        async with s.get(url, headers={"User-Agent": USER_AGENT},
                         timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                return 200, await r.json()
            return r.status, None


class EspnProvider:
    BASE_BACKOFF = 30          # seconds — first cool-down when blocked
    MAX_BACKOFF = 15 * 60      # cap the exponential backoff

    def __init__(self, http_get=None):
        self._http_get = http_get or _aiohttp_get
        self._blocked_until = 0.0      # monotonic deadline; >now ⇒ circuit open
        self._backoff_step = 0

    # ── circuit-breaker ─────────────────────────────────────────────────────────
    def is_blocked(self, now: float | None = None) -> bool:
        now = self._now() if now is None else now
        return now < self._blocked_until

    def _now(self) -> float:
        # monotonic (not loop.time()): independent of which event loop is running, so the
        # circuit-breaker deadline is stable across reconnects and unit-test loops.
        return time.monotonic()

    def _next_backoff(self) -> float:
        delay = min(self.BASE_BACKOFF * (2 ** self._backoff_step), self.MAX_BACKOFF)
        self._backoff_step += 1
        return delay

    def _trip(self) -> None:
        self._blocked_until = self._now() + self._next_backoff()

    def _reset(self) -> None:
        self._backoff_step = 0
        self._blocked_until = 0.0

    # ── fetch ───────────────────────────────────────────────────────────────────
    async def fetch_matches(self, et_day: str | None = None):
        """The one shared scoreboard call → all matches as NormMatch. [] on a soft error or
        while the circuit is open; raises BlockedError the moment a 429/403 trips it."""
        if self.is_blocked():
            return []
        url = SCOREBOARD_URL
        if et_day:
            url = f"{url}?dates={et_day.replace('-', '')}"
        status, payload = await self._http_get(url)
        if status in (429, 403):
            self._trip()
            logger.warning("EspnProvider blocked (HTTP %d); backing off", status)
            raise BlockedError(f"ESPN HTTP {status}")
        if status != 200 or not payload:
            logger.warning("EspnProvider HTTP %s", status)
            return []
        self._reset()
        return scoreboard_to_matches(payload)
