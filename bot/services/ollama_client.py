"""Optional Ollama local-LLM integration.

The bot NEVER calls Ollama without retrieved context — hallucination is
prevented by always providing the FAQ snippets as the sole information source.
"""

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a helpful, warm, and professional assistant for NJIT's "
    "Graduate Student Association (GSA). Answer the student's question "
    "using ONLY the context provided below. If the context does not contain "
    "enough information, say so politely and suggest the student contact a "
    "GSA officer. Never invent facts."
)


class OllamaClient:
    """Thin async wrapper around the Ollama REST API."""

    def __init__(self, model: str = "llama3", base_url: str = "http://localhost:11434") -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def generate_answer(self, question: str, context: str) -> Optional[str]:
        """Return an LLM-generated answer grounded in *context*, or None on failure."""
        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"--- CONTEXT ---\n{context}\n--- END CONTEXT ---\n\n"
            f"Student question: {question}\n\nAnswer:"
        )
        payload = {"model": self.model, "prompt": prompt, "stream": False}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("Ollama returned HTTP %d", resp.status)
                        return None
                    data = await resp.json()
                    return data.get("response", "").strip() or None
        except aiohttp.ClientConnectorError:
            logger.warning("Ollama is not reachable at %s", self.base_url)
            return None
        except Exception as exc:
            logger.error("Ollama error: %s", exc)
            return None

    async def is_available(self) -> bool:
        """Quick health check — returns True if Ollama responds."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/api/tags",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False
