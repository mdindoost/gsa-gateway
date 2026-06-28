"""Tests for OllamaClient — all HTTP calls are mocked."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.services.ollama_client import OllamaClient
from bot.services.retriever import RetrievedChunk
from bot.services import ollama_client as oc


def make_chunk(text: str = "Answer: some info") -> RetrievedChunk:
    return RetrievedChunk(
        text=text,
        source_file="gsa_faq.md",
        source_type="faq",
        section_title="FAQ Section",
        similarity=0.85,
        relevance_score=0.85,
        metadata={},
    )


@pytest.fixture
def client() -> OllamaClient:
    return OllamaClient(
        base_url="http://localhost:11434",
        model="llama3.1:8b",
        timeout=30,
    )


def _mock_session_with_response(status: int = 200, json_data: dict[str, Any] | None = None):
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data or {"response": "Test answer."})
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.post = MagicMock(return_value=resp)
    session.closed = False

    return session


class TestGenerateAnswer:
    @pytest.mark.asyncio
    async def test_returns_response_text(self, client: OllamaClient) -> None:
        session = _mock_session_with_response(200, {"response": "GSA stands for Graduate Student Association."})
        client._session = session
        chunks = [make_chunk("Question: What is GSA?\nAnswer: It is the student body.")]
        result = await client.generate_answer("What is GSA?", chunks)
        assert result == "GSA stands for Graduate Student Association."

    @pytest.mark.asyncio
    async def test_prompt_includes_context(self, client: OllamaClient) -> None:
        session = _mock_session_with_response(200, {"response": "Answer here."})
        client._session = session
        chunks = [make_chunk("Question: How do I join?\nAnswer: Attend a meeting.")]
        await client.generate_answer("How do I join?", chunks)
        payload = session.post.call_args[1]["json"]
        assert "How do I join?" in payload["prompt"]
        assert "system" in payload

    @pytest.mark.asyncio
    async def test_returns_none_when_no_chunks(self, client: OllamaClient) -> None:
        result = await client.generate_answer("question", [])
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self, client: OllamaClient) -> None:
        session = _mock_session_with_response(500, {})
        client._session = session
        chunks = [make_chunk()]
        result = await client.generate_answer("question", chunks)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response(self, client: OllamaClient) -> None:
        session = _mock_session_with_response(200, {"response": "   "})
        client._session = session
        chunks = [make_chunk()]
        result = await client.generate_answer("question", chunks)
        assert result is None

    @pytest.mark.asyncio
    async def test_response_is_stripped(self, client: OllamaClient) -> None:
        session = _mock_session_with_response(200, {"response": "\n\n  Trimmed answer.  \n"})
        client._session = session
        chunks = [make_chunk()]
        result = await client.generate_answer("question", chunks)
        assert result == "Trimmed answer."

    @pytest.mark.asyncio
    async def test_connection_error_returns_none(self, client: OllamaClient) -> None:
        session = MagicMock()
        session.closed = False
        resp = AsyncMock()
        resp.__aenter__ = AsyncMock(side_effect=RuntimeError("connection refused"))
        resp.__aexit__ = AsyncMock(return_value=False)
        session.post = MagicMock(return_value=resp)
        client._session = session
        result = await client.generate_answer("question", [make_chunk()])
        assert result is None

    @pytest.mark.asyncio
    async def test_conversation_history_in_system_prompt(self, client: OllamaClient) -> None:
        session = _mock_session_with_response(200, {"response": "Step 2 is..."})
        client._session = session
        chunks = [make_chunk()]
        history = [
            {"role": "user", "content": "tell me the steps"},
            {"role": "assistant", "content": "Step 1 is to submit..."},
        ]
        await client.generate_answer("what about step 2?", chunks, conversation_history=history)
        payload = session.post.call_args[1]["json"]
        assert "Step 1" in payload["system"]


class TestCheckConnection:
    @pytest.mark.asyncio
    async def test_returns_true_when_ollama_up(self, client: OllamaClient) -> None:
        session = _mock_session_with_response(200, {"response": "ok"})
        client._session = session
        result = await client.check_connection()
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_http_error(self, client: OllamaClient) -> None:
        session = _mock_session_with_response(500, {})
        client._session = session
        result = await client.check_connection()
        assert result is False

    @pytest.mark.asyncio
    async def test_never_raises(self, client: OllamaClient) -> None:
        session = MagicMock()
        session.closed = False
        resp = AsyncMock()
        resp.__aenter__ = AsyncMock(side_effect=RuntimeError("unexpected error"))
        resp.__aexit__ = AsyncMock(return_value=False)
        session.post = MagicMock(return_value=resp)
        client._session = session
        result = await client.check_connection()
        assert result is False


class TestEstimateTokens:
    def test_overcounts_vs_raw_tiktoken(self):
        # estimate must be >= the raw tiktoken count (the safety factor) for adversarial inputs
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        for s in [
            "https://www.njit.edu/global/h1b-cap-gap?year=2026&term=fall",
            "def f(x):\n  return x*x  # minified",
            "学生签证 OPT STEM 延期 申请",
            "AAAA1234-BBBB5678-CCCC9012",
            "the cap gap period bridges F-1 status to H-1B",
        ]:
            assert oc._estimate_tokens(s) >= len(enc.encode(s))

    def test_empty_is_zero(self):
        assert oc._estimate_tokens("") == 0

    def test_fallback_is_pessimistic_when_tiktoken_unavailable(self, monkeypatch):
        # force the fallback path; byte count is always >= true token count
        monkeypatch.setattr(oc, "_TIKTOKEN_ENC", None)
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        for s in ["https://x.njit.edu/a?b=c", "学生签证延期", "plain english text"]:
            assert oc._estimate_tokens(s) >= len(enc.encode(s))
            assert oc._estimate_tokens(s) == len(s.encode("utf-8"))


class TestNumCtxConfig:
    def test_default_is_16384(self):
        assert OllamaClient(base_url="http://x", model="m").num_ctx == 16384

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_NUM_CTX", "12000")
        assert OllamaClient(base_url="http://x", model="m").num_ctx == 12000

    def test_constructor_arg_wins(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_NUM_CTX", "12000")
        assert OllamaClient(base_url="http://x", model="m", num_ctx=9000).num_ctx == 9000


class TestFitChunks:
    def _client(self, num_ctx):
        return OllamaClient(base_url="http://x", model="m", num_ctx=num_ctx)

    def test_under_budget_is_identity(self):
        c = self._client(16384)
        chunks = [make_chunk("short a"), make_chunk("short b")]
        fitted = c._fit_chunks(chunks, "sys", "q", num_predict=512)
        assert fitted == chunks  # same objects, untouched

    def test_drops_lowest_ranked_until_fit(self):
        # word*200 makes each chunk ~407 est tokens so 2 fit (677<=720) but 3 don't (947>720).
        # budget = 2000 - 256 - CONTEXT_CUSHION_TOKENS = 720.
        c = self._client(2000)  # tiny window forces dropping
        big = "word " * 200
        chunks = [make_chunk("rank1 " + big), make_chunk("rank2 " + big), make_chunk("rank3 " + big)]
        fitted = c._fit_chunks(chunks, "sys", "q", num_predict=256)
        assert len(fitted) < 3
        assert fitted[0] is chunks[0]  # rank-1 kept, input order preserved
        # rendered prompt is within budget
        user = c._assemble_user(c._build_context_block(fitted), "q")
        assert oc._estimate_tokens("sys") + oc._estimate_tokens(user) + 256 <= 2000

    def test_single_page_overflow_is_prefix_truncated(self):
        # num_ctx=2048 → enforced doc budget = 2048 - CONTEXT_CUSHION_TOKENS - 128 = 896.
        c = self._client(2048)
        body = "FIRST SENTENCE is the answer. " + ("filler tail " * 2000)
        chunks = [make_chunk(body)]
        fitted = c._fit_chunks(chunks, "sys", "q", num_predict=128)
        assert len(fitted) == 1
        assert fitted[0] is not chunks[0]               # a copy, not the original
        assert chunks[0].text == body                   # original unmutated
        assert "FIRST SENTENCE is the answer." in fitted[0].text
        assert oc.TRUNCATION_NOTE.strip()[:20] in fitted[0].text
        user = c._assemble_user(c._build_context_block(fitted), "q")
        # system+user must fit the real doc budget (num_ctx - cushion - num_predict)
        assert (oc._estimate_tokens("sys") + oc._estimate_tokens(user)
                <= 2048 - oc.CONTEXT_CUSHION_TOKENS - 128)

    def test_truncated_copy_preserves_provenance(self):
        c = self._client(2048)
        ch = make_chunk("answer. " + ("x " * 4000))
        ch.item_id = 27226           # dynamic non-field attrs (as the runtime shim sets)
        ch.source_url = "https://www.njit.edu/global/h1b"
        ch.verified = True
        fitted = c._fit_chunks([ch], "sys", "q", num_predict=128)
        assert getattr(fitted[0], "item_id") == 27226
        assert getattr(fitted[0], "source_url") == "https://www.njit.edu/global/h1b"
        assert getattr(fitted[0], "verified") is True

    def test_empty_input_returns_empty(self):
        assert self._client(16384)._fit_chunks([], "sys", "q", 512) == []

    def test_degenerate_budget_returns_empty(self):
        c = self._client(300)  # system+framing+num_predict+cushion already blow the window
        fitted = c._fit_chunks([make_chunk("x " * 5000)], "huge " * 200, "q", num_predict=128)
        assert fitted == []

    def test_no_whitespace_hard_cut(self):
        # num_ctx=2048 → budget 896; 896+128=1024 < 2048.  Hard-cuts "A"*N (no spaces/newlines).
        c = self._client(2048)
        chunks = [make_chunk("A" * 40000)]  # no whitespace at all
        fitted = c._fit_chunks(chunks, "sys", "q", num_predict=128)
        assert len(fitted) == 1
        # confirm the hard-cut path ran: note appended, and the pre-note body has NO space
        marker = oc.TRUNCATION_NOTE.strip()[:20]
        assert marker in fitted[0].text
        body_part = fitted[0].text.split(marker)[0]
        assert " " not in body_part  # hard-cut "A"*N, not a whitespace-snap
        user = c._assemble_user(c._build_context_block(fitted), "q")
        # system+user must fit the real doc budget (num_ctx - cushion - num_predict)
        assert (oc._estimate_tokens("sys") + oc._estimate_tokens(user)
                <= 2048 - oc.CONTEXT_CUSHION_TOKENS - 128)


class TestGuardWiring:
    def _client(self, num_ctx=16384):
        return OllamaClient(base_url="http://x", model="m", num_ctx=num_ctx)

    @pytest.mark.asyncio
    async def test_assembled_prompt_within_budget_on_huge_bundle(self):
        c = self._client(16384)
        c._session = _mock_session_with_response(200, {"response": "Cap gap is the period ..."})
        long = "The H-1B cap-gap period bridges F-1 OPT to H-1B. " + ("policy detail " * 1500)
        chunks = [make_chunk(long) for _ in range(5)]
        await c.generate_answer("what is the H-1B cap gap period for F-1 students", chunks)
        payload = c._session.post.call_args[1]["json"]
        total = oc._estimate_tokens(payload["system"]) + oc._estimate_tokens(payload["prompt"]) + 512
        assert total <= 16384
        assert payload["options"]["num_ctx"] == 16384

    @pytest.mark.asyncio
    async def test_cap_gap_first_sentence_survives(self):
        c = self._client(16384)
        c._session = _mock_session_with_response(200, {"response": "ok"})
        chunks = [make_chunk("The cap-gap period extends F-1 status. " + ("x " * 1000))] + \
                 [make_chunk("y " * 2000) for _ in range(4)]
        await c.generate_answer("cap gap period", chunks)
        assert "The cap-gap period extends F-1 status." in c._session.post.call_args[1]["json"]["prompt"]

    @pytest.mark.asyncio
    async def test_history_bounded_to_max_turns(self):
        c = self._client(16384)
        c._session = _mock_session_with_response(200, {"response": "ok"})
        history = [{"role": "user", "content": f"turn{i}"} for i in range(20)]
        await c.generate_answer("q", [make_chunk("a")], conversation_history=history)
        sys = c._session.post.call_args[1]["json"]["system"]
        assert "turn19" in sys and "turn0" not in sys  # only the last MAX_HISTORY_TURNS kept

    @pytest.mark.asyncio
    async def test_empty_fitted_returns_none(self):
        c = self._client(300)  # degenerate window
        c._session = _mock_session_with_response(200, {"response": "should not be used"})
        result = await c.generate_answer("q " * 50, [make_chunk("z " * 5000)],
                                         conversation_history=[{"role": "user", "content": "h " * 300}])
        assert result is None

    @pytest.mark.asyncio
    async def test_no_additional_sources_block(self):
        c = self._client(16384)
        c._session = _mock_session_with_response(200, {"response": "ok"})
        await c.generate_answer("q", [make_chunk("a " * 2000) for _ in range(4)])
        assert c._session.post.call_args is not None, "POST must be called (at least one chunk fits)"
        assert "ADDITIONAL SOURCES" not in c._session.post.call_args[1]["json"]["prompt"]

    def test_build_system_prompt_keeps_exactly_max_turns(self):
        c = self._client(16384)
        # exactly MAX_HISTORY_TURNS: all kept, history framing present (≤6 = old behavior)
        six = [{"role": "user", "content": f"turn{i}"} for i in range(oc.MAX_HISTORY_TURNS)]
        sys6 = c._build_system_prompt(six)
        assert all(f"turn{i}" in sys6 for i in range(oc.MAX_HISTORY_TURNS))
        assert "=== CONVERSATION HISTORY ===" in sys6
        assert "=== END OF CONVERSATION HISTORY ===" in sys6
        # one more turn than the cap: the oldest is dropped, the rest survive
        seven = [{"role": "user", "content": f"turn{i}"} for i in range(oc.MAX_HISTORY_TURNS + 1)]
        sys7 = c._build_system_prompt(seven)
        assert "turn0" not in sys7
        assert all(f"turn{i}" in sys7 for i in range(1, oc.MAX_HISTORY_TURNS + 1))


class TestComposeBudget:
    def _client(self, num_ctx):
        return OllamaClient(base_url="http://x", model="m", num_ctx=num_ctx)

    @pytest.mark.asyncio
    async def test_over_budget_facts_return_none_without_calling_ollama(self):
        c = self._client(2000)
        c._session = _mock_session_with_response(200, {"response": "should not be used"})
        result = await c.compose_from_rows("list everyone", "name, " * 5000)
        assert result is None
        c._session.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_under_budget_facts_compose_normally(self):
        c = self._client(16384)
        c._session = _mock_session_with_response(200, {"response": "Here are the officers: ..."})
        result = await c.compose_from_rows("who are the officers", "President: A\nVP: B")
        assert result == "Here are the officers: ..."
