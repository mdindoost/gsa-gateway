import aiohttp
import asyncio
import logging
import datetime

BASE_URL = "https://api.football-data.org/v4"
COMPETITION = "WC"

MATCH_STATUS = {
    "SCHEDULED": "scheduled",
    "TIMED": "scheduled",
    "IN_PLAY": "live",
    "PAUSED": "halftime",
    "FINISHED": "finished",
    "POSTPONED": "postponed",
    "CANCELLED": "cancelled",
}

WC_EMBLEM = "https://crests.football-data.org/wm26.png"


class FootballClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {"X-Auth-Token": api_key}
        self._session = None
        self.logger = logging.getLogger(__name__)

    async def _get(self, endpoint: str) -> dict:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

        url = BASE_URL + endpoint
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with self._session.get(url, headers=self.headers, timeout=timeout) as resp:
                if resp.status == 429:
                    self.logger.warning("Rate limited — waiting 60s")
                    await asyncio.sleep(60)
                    return {}
                if resp.status == 403:
                    self.logger.error("Invalid API key or competition not available")
                    return {}
                if resp.status != 200:
                    self.logger.error("Request failed: %s %s", resp.status, url)
                    return {}
                return await resp.json()
        except Exception as e:
            self.logger.error("Error fetching %s: %s", url, e)
            return {}

    async def get_todays_matches(self) -> list:
        data = await self._get("/competitions/WC/matches")
        today = datetime.date.today().isoformat()
        matches = data.get("matches", [])
        return [m for m in matches if m.get("utcDate", "")[:10] == today]

    async def get_live_matches(self) -> list:
        in_play = await self._get("/competitions/WC/matches?status=IN_PLAY")
        paused = await self._get("/competitions/WC/matches?status=PAUSED")
        return in_play.get("matches", []) + paused.get("matches", [])

    async def get_match(self, match_id: int) -> dict:
        """Fetch a single match with goals unfolded (X-Unfold-Goals header)."""
        extra = {**self.headers, "X-Unfold-Goals": "true"}
        url = f"{BASE_URL}/matches/{match_id}"
        timeout = aiohttp.ClientTimeout(total=10)
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        try:
            async with self._session.get(url, headers=extra, timeout=timeout) as resp:
                if resp.status == 429:
                    self.logger.warning("Rate limited — waiting 60s")
                    await asyncio.sleep(60)
                    return {}
                if resp.status not in (200, 403):
                    self.logger.error("get_match failed: %s %s", resp.status, url)
                    return {}
                return await resp.json()
        except Exception as e:
            self.logger.error("Error fetching match %s: %s", match_id, e)
            return {}

    async def get_standings(self) -> dict:
        return await self._get("/competitions/WC/standings")

    async def get_upcoming_matches(self, days: int = 7) -> list:
        data = await self._get("/competitions/WC/matches")
        today = datetime.date.today()
        cutoff = today + datetime.timedelta(days=days)
        matches = data.get("matches", [])
        filtered = [
            m for m in matches
            if m.get("status") in ("TIMED", "SCHEDULED")
            and today.isoformat() <= m.get("utcDate", "")[:10] <= cutoff.isoformat()
        ]
        return sorted(filtered, key=lambda m: m.get("utcDate", ""))

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
