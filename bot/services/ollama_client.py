"""Optional Ollama local-LLM integration.

The bot NEVER calls Ollama without retrieved KB context — hallucination is
prevented by always prepending FAQ snippets as the sole information source.
"""

import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

_ASK_SYSTEM = (
    "You are GSA Gateway, the AI assistant for NJIT's Graduate Student Association. "
    "You have been given context from the GSA knowledge base below.\n\n"
    "Answer the student's question using the context provided. Rules:\n"
    "- If the context contains the answer, give it confidently and completely — "
    "include names, emails, locations, and specific details.\n"
    "- If the context partially answers the question, give what you know and note "
    "what might need verification.\n"
    "- Only say you don't know if the context has truly no relevant information at all.\n"
    "- Never invent facts, names, emails, or dates not present in the context.\n"
    "- Keep your answer under 200 words.\n"
    "- Write in plain text for Discord: no markdown headers (# ##). "
    "Simple bullet points (- item) are fine.\n"
    "- Be warm, helpful, and specific. Students trust you for accurate GSA information."
)

_SUMMARY_SYSTEM = (
    "You are helping GSA officers prepare a weekly student-engagement report. "
    "You will receive initiative submissions and feedback from the past week. "
    "Your tasks:\n"
    "1. Group items by theme (e.g. academic support, social events, funding, wellness).\n"
    "2. Identify the top 3 student concerns or requests.\n"
    "3. Suggest 2-3 concrete action items for GSA officers.\n"
    "Write clearly and professionally. Use simple bullet points. "
    "Keep the entire output under 400 words. Do not invent information."
)


class OllamaClient:
    """Thin async wrapper around the Ollama REST API."""

    def __init__(
        self,
        model: str = "llama3",
        base_url: str = "http://localhost:11434",
        timeout: int = 90,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def _post(self, payload: dict[str, Any]) -> Optional[str]:
        """Send a /api/generate request; return stripped response text or None."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                    timeout=self._timeout,
                ) as resp:
                    if resp.status != 200:
                        logger.warning("Ollama returned HTTP %d", resp.status)
                        return None
                    data = await resp.json()
                    return data.get("response", "").strip() or None
        except aiohttp.ClientConnectorError:
            logger.warning("Ollama unreachable at %s — falling back to KB text", self.base_url)
            return None
        except TimeoutError:
            logger.warning("Ollama timed out — falling back to KB text")
            return None
        except Exception as exc:
            logger.error("Ollama unexpected error: %s", exc)
            return None

    async def generate_answer(
        self,
        question: str,
        context_chunks: list[str],
        model: Optional[str] = None,
    ) -> Optional[str]:
        """Return an AI answer grounded in retrieved KB chunks.

        context_chunks — list of "Q: ...\\nA: ..." strings (top search results).
        Caps at 3 chunks. Returns None if Ollama is unreachable or empty.
        """
        context = "\n\n".join(context_chunks[:3])
        prompt = (
            f"--- CONTEXT ---\n{context}\n--- END CONTEXT ---\n\n"
            f"Student question: {question}\n\nAnswer:"
        )
        return await self._post(
            {
                "model": model or self.model,
                "system": _ASK_SYSTEM,
                "prompt": prompt,
                "stream": False,
            }
        )

    async def generate(self, prompt: str, system: str) -> Optional[str]:
        """General-purpose generate call with an explicit system prompt."""
        return await self._post(
            {"model": self.model, "system": system, "prompt": prompt, "stream": False}
        )

    async def check_connection(self) -> bool:
        """Health check — logs a warning if Ollama is unreachable, never raises."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/api/tags",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        logger.info(
                            "Ollama is reachable at %s (model: %s)",
                            self.base_url,
                            self.model,
                        )
                        return True
                    logger.warning("Ollama health check failed: HTTP %d", resp.status)
                    return False
        except Exception:
            logger.warning(
                "Ollama is not reachable at %s — AI answers disabled until it restarts",
                self.base_url,
            )
            return False

    async def is_available(self) -> bool:
        """Alias for check_connection (backward compat)."""
        return await self.check_connection()
