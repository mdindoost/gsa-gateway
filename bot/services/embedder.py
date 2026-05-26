"""Embedding service — converts text to vectors using Ollama's nomic-embed-text model."""

import asyncio
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(
        self,
        base_url: str,
        model: str = "nomic-embed-text",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.embed_url = f"{self.base_url}/api/embed"
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=10)
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def embed_text(
        self,
        text: str,
        timeout: int = 30,
    ) -> Optional[list[float]]:
        text = text.strip()[:2000]
        if not text:
            return None
        try:
            session = await self._get_session()
            async with session.post(
                self.embed_url,
                json={"model": self.model, "input": text},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    logger.warning("Embed API returned HTTP %d", resp.status)
                    return None
                data = await resp.json()
                embeddings = data.get("embeddings")
                if not embeddings or not embeddings[0]:
                    logger.warning("Embed API returned empty embeddings")
                    return None
                return embeddings[0]
        except asyncio.TimeoutError:
            logger.warning("Embedding timeout after %ds for text: '%s...'", timeout, text[:40])
            return None
        except aiohttp.ClientConnectorError as exc:
            logger.warning("Embedding service unreachable: %s", exc)
            return None
        except Exception as exc:
            logger.warning("Embedding error: %s", exc)
            return None

    async def embed_batch(
        self,
        texts: list[str],
        batch_size: int = 10,
    ) -> list[Optional[list[float]]]:
        results: list[Optional[list[float]]] = [None] * len(texts)
        total_batches = (len(texts) + batch_size - 1) // batch_size

        for batch_num in range(total_batches):
            start = batch_num * batch_size
            end = min(start + batch_size, len(texts))
            batch = texts[start:end]
            logger.info("Embedding batch %d/%d (%d texts)...", batch_num + 1, total_batches, len(batch))
            tasks = [self.embed_document(t) for t in batch]
            batch_results = await asyncio.gather(*tasks)
            for i, result in enumerate(batch_results):
                results[start + i] = result

        return results

    async def embed_query(self, query: str) -> Optional[list[float]]:
        return await self.embed_text(f"search_query: {query}")

    async def embed_document(self, text: str) -> Optional[list[float]]:
        return await self.embed_text(f"search_document: {text}")

    async def check_connection(self) -> bool:
        result = await self.embed_text("test", timeout=10)
        if result is not None:
            logger.info("Embedding service connected (model=%s)", self.model)
            return True
        logger.warning("Embedding service not available (model=%s)", self.model)
        return False

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
