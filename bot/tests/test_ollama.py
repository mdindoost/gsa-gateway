"""Tests for OllamaClient — all HTTP calls are mocked."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.services.ollama_client import OllamaClient, _ASK_SYSTEM


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client() -> OllamaClient:
    return OllamaClient(model="llama3", base_url="http://localhost:11434", timeout=30)


def _mock_response(status: int = 200, json_data: dict[str, Any] | None = None):
    """Build a mock aiohttp response context manager."""
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data or {"response": "Test answer."})
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _mock_session(response_cm):
    """Build a mock aiohttp.ClientSession context manager."""
    session = AsyncMock()
    session.post = MagicMock(return_value=response_cm)
    session.get = MagicMock(return_value=response_cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    return session_cm, session


# ── generate_answer ───────────────────────────────────────────────────────────

class TestGenerateAnswer:
    @pytest.mark.asyncio
    async def test_returns_response_text(self, client: OllamaClient) -> None:
        resp_cm = _mock_response(200, {"response": "GSA stands for Graduate Student Association."})
        session_cm, session = _mock_session(resp_cm)
        with patch("aiohttp.ClientSession", return_value=session_cm):
            result = await client.generate_answer("What is GSA?", ["Q: What is GSA?\nA: It is the student body."])
        assert result == "GSA stands for Graduate Student Association."

    @pytest.mark.asyncio
    async def test_prompt_includes_context_chunks(self, client: OllamaClient) -> None:
        resp_cm = _mock_response(200, {"response": "Answer here."})
        session_cm, session = _mock_session(resp_cm)
        with patch("aiohttp.ClientSession", return_value=session_cm):
            await client.generate_answer(
                "How do I join?",
                ["Q: How do I join?\nA: Attend a meeting.", "Q: Events?\nA: Check events.yml."],
            )
        call_kwargs = session.post.call_args
        payload = call_kwargs[1]["json"] if call_kwargs[1] else call_kwargs[0][1]
        assert "Q: How do I join?" in payload["prompt"]
        assert "Q: Events?" in payload["prompt"]
        assert payload["system"] == _ASK_SYSTEM

    @pytest.mark.asyncio
    async def test_caps_at_three_context_chunks(self, client: OllamaClient) -> None:
        resp_cm = _mock_response(200, {"response": "OK."})
        session_cm, session = _mock_session(resp_cm)
        chunks = [f"Q: Q{i}?\nA: A{i}." for i in range(6)]
        with patch("aiohttp.ClientSession", return_value=session_cm):
            await client.generate_answer("question", chunks)
        payload = session.post.call_args[1]["json"]
        # chunk 4 and 5 must not appear in the prompt
        assert "Q: Q4?" not in payload["prompt"]
        assert "Q: Q5?" not in payload["prompt"]

    @pytest.mark.asyncio
    async def test_returns_none_on_connection_error(self, client: OllamaClient) -> None:
        import aiohttp
        with patch("aiohttp.ClientSession") as MockSession:
            MockSession.return_value.__aenter__ = AsyncMock(side_effect=aiohttp.ClientConnectorError(None, OSError()))  # type: ignore[arg-type]
            result = await client.generate_answer("What is GSA?", ["some context"])
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self, client: OllamaClient) -> None:
        resp_cm = _mock_response(500, {})
        session_cm, _ = _mock_session(resp_cm)
        with patch("aiohttp.ClientSession", return_value=session_cm):
            result = await client.generate_answer("question", ["context"])
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response(self, client: OllamaClient) -> None:
        resp_cm = _mock_response(200, {"response": "   "})
        session_cm, _ = _mock_session(resp_cm)
        with patch("aiohttp.ClientSession", return_value=session_cm):
            result = await client.generate_answer("question", ["context"])
        assert result is None

    @pytest.mark.asyncio
    async def test_response_is_stripped(self, client: OllamaClient) -> None:
        resp_cm = _mock_response(200, {"response": "\n\n  Trimmed answer.  \n"})
        session_cm, _ = _mock_session(resp_cm)
        with patch("aiohttp.ClientSession", return_value=session_cm):
            result = await client.generate_answer("question", ["context"])
        assert result == "Trimmed answer."


# ── check_connection ──────────────────────────────────────────────────────────

class TestCheckConnection:
    @pytest.mark.asyncio
    async def test_returns_true_when_ollama_up(self, client: OllamaClient) -> None:
        resp_cm = _mock_response(200, {"models": []})
        session_cm, _ = _mock_session(resp_cm)
        with patch("aiohttp.ClientSession", return_value=session_cm):
            assert await client.check_connection() is True

    @pytest.mark.asyncio
    async def test_returns_false_when_ollama_down(self, client: OllamaClient) -> None:
        import aiohttp
        with patch("aiohttp.ClientSession") as MockSession:
            MockSession.return_value.__aenter__ = AsyncMock(
                side_effect=aiohttp.ClientConnectorError(None, OSError())  # type: ignore[arg-type]
            )
            assert await client.check_connection() is False

    @pytest.mark.asyncio
    async def test_never_raises(self, client: OllamaClient) -> None:
        with patch("aiohttp.ClientSession", side_effect=RuntimeError("boom")):
            result = await client.check_connection()
        assert result is False
