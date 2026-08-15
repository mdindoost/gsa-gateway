"""Tests for TelegramConnector command handlers."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.connectors.telegram_connector import TelegramConnector
from bot.core.message_handler import MessageResponse
from bot.services.knowledge_base import Contact, Event, Resource


@pytest.fixture
def kb():
    kb = MagicMock()
    kb.events = [
        Event(
            name="GSA Mixer",
            date="2099-06-10",
            time="6:00 PM",
            location="Campus Center",
            description="Annual spring mixer.",
            organizer="GSA",
            rsvp_link="",
        )
    ]
    kb.contacts = {
        "president": Contact(
            role="GSA President",
            name="Fernando Vera",
            email="gsa-pres@njit.edu",
            office="Campus Center 110A",
        )
    }
    kb.resources = {
        "academic": [
            Resource(
                title="NJIT Library",
                description="Research databases.",
                url="https://library.njit.edu",
                category="academic",
            )
        ]
    }
    return kb


@pytest.fixture
def connector(kb):
    handler = MagicMock()
    handler.handle = AsyncMock(
        return_value=MessageResponse(text="Hello from GSA Gateway!")
    )
    return TelegramConnector(token="fake-token", handler=handler, kb=kb)


def _make_update_context(text="hello", args=None):
    message = MagicMock()
    message.text = text
    message.reply_text = AsyncMock()
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = 12345
    update.message = message
    context = MagicMock()
    context.args = args or []
    return update, context


@pytest.mark.asyncio
async def test_on_message_calls_handler_and_replies(connector):
    update, context = _make_update_context("what is gsa?")
    await connector._on_message(update, context)
    connector.handler.handle.assert_called_once()
    update.message.reply_text.assert_called_once()
    reply_text = update.message.reply_text.call_args[0][0]
    assert "hello from gsa gateway" in reply_text.lower()


@pytest.mark.asyncio
async def test_on_message_appends_source_note(connector):
    connector.handler.handle = AsyncMock(
        return_value=MessageResponse(text="GSA info.", source_note="GSA FAQ")
    )
    update, context = _make_update_context("tell me about gsa")
    await connector._on_message(update, context)
    reply_text = update.message.reply_text.call_args[0][0]
    assert "gsa faq" in reply_text.lower()


@pytest.mark.asyncio
async def test_on_message_skips_empty_response(connector):
    connector.handler.handle = AsyncMock(return_value=MessageResponse(text=""))
    update, context = _make_update_context("  ")
    await connector._on_message(update, context)
    update.message.reply_text.assert_not_called()


# ── Unified mode dispatch (judging routes through the dispatcher) ──────────────

@pytest.fixture
def judging_setup():
    """A real JudgingSessionManager wired into a connector through a ModeDispatcher that
    shares the conversation ModeStore — the production wiring. Yields the shared store too."""
    import os
    import tempfile
    os.environ.setdefault("GSA_JUDGING_SCRYPT_N", "64")
    from bot.core.modes import ConversationModeStore, ModeDispatcher, ModeRegistry
    from v2.core.database.schema import create_all
    from v2.core.judging import db as jdb
    from v2.core.judging.session import JudgingSessionManager

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = create_all(db_path)
    eid = jdb.create_event(conn, "3MRP", criteria=["Q1"], top_n=1, score_min=1, score_max=5)
    jdb.set_event_status(conn, eid, "open")
    jdb.add_judge(conn, eid, "Amira", "J-001")
    conn.commit()
    conn.close()

    handler = MagicMock()
    handler.handle = AsyncMock(return_value=MessageResponse(text="RAG answer", question_id=7))
    judging = JudgingSessionManager(db_path)
    store = ConversationModeStore()
    registry = ModeRegistry(store, judging=judging)
    dispatcher = ModeDispatcher(registry, judging=judging, conversation_handler=handler.handle)
    connector = TelegramConnector(
        token="fake", handler=handler, kb=MagicMock(),
        judging_manager=judging, dispatcher=dispatcher,
    )
    yield connector, handler, store
    os.unlink(db_path)


@pytest.mark.asyncio
async def test_judging_trigger_routes_to_judging_not_handler(judging_setup):
    connector, handler, _store = judging_setup
    update, context = _make_update_context("judge mode")
    await connector._on_message(update, context)
    handler.handle.assert_not_called()                 # judging owned it
    reply_text = update.message.reply_text.call_args[0][0]
    assert "PIN" in reply_text


@pytest.mark.asyncio
async def test_idle_normal_message_routes_to_handler(judging_setup):
    connector, handler, _store = judging_setup
    update, context = _make_update_context("what is gsa?")
    await connector._on_message(update, context)
    handler.handle.assert_called_once()                # conversation owned it
    reply_text = update.message.reply_text.call_args[0][0]
    assert "rag answer" in reply_text.lower()


@pytest.mark.asyncio
async def test_judge_midflow_number_stays_in_judging(judging_setup):
    connector, handler, _store = judging_setup
    for text in ("judge mode", "J-001"):              # authenticate -> ready
        u, c = _make_update_context(text)
        await connector._on_message(u, c)
    handler.handle.reset_mock()
    # Now in JUDGE mode; a bare number is judging input, NOT a RAG question.
    u, c = _make_update_context("100")
    await connector._on_message(u, c)
    handler.handle.assert_not_called()


@pytest.mark.asyncio
async def test_toggle_phrase_midjudging_owned_by_judging_not_store(judging_setup):
    # A judge mid-flow typing "free mode" must stay owned by judging (already in a judging
    # mode) and must NOT flip the shared conversation store to FREE behind their back.
    from bot.core.modes import Mode
    connector, handler, store = judging_setup
    for text in ("judge mode", "J-001"):              # -> ready (JUDGE)
        u, c = _make_update_context(text)
        await connector._on_message(u, c)
    handler.handle.reset_mock()
    u, c = _make_update_context("free mode")
    await connector._on_message(u, c)
    handler.handle.assert_not_called()                # judging owned it
    assert store.get("12345") == Mode.GSA             # conversation store untouched


# NOTE: tests for /events /contact /resources /help were removed — those v1 command
# handlers no longer exist on TelegramConnector (all-conversational migration; only
# /start + /qrcode remain). They were already failing on the base branch.


# ── Long-answer splitting (2026-08-14) ───────────────────────────────────────
# Regression suite for the bug where "who is Taro Narahara" returned NOTHING on Telegram:
# the ~7,100-char answer exceeded the 4,096 cap, the send 400'd, and the except-fallback
# re-sent the SAME oversized text so it 400'd too. Spec:
# docs/superpowers/specs/2026-08-14-telegram-message-split-design.md

from telegram.error import BadRequest, RetryAfter  # noqa: E402

from bot.connectors.telegram_connector import TG_LIMIT  # noqa: E402
from bot.core.msg_split import utf16_len  # noqa: E402

# A realistic stand-in for Narahara's entity card (about + research statement + courses +
# service + Scholar links). Measured live at ~7,100 characters.
LONG_ANSWER = (
    "Taro Narahara\nAssociate Professor — New Jersey School of Architecture\n\n"
    + "\n\n".join(
        f"Research paragraph {i}: computational design, machine learning & VR. " * 12
        for i in range(9)
    )
    + "\n\n🎓 [Google Scholar](https://scholar.google.com/citations?hl=en&user=RRVZtWgAAAAJ)"
)


def _strict_reply_text():
    """A reply_text that behaves like the real Telegram API: 400s on anything over 4096."""
    async def _send(text, **kwargs):
        if utf16_len(text) > TG_LIMIT:
            raise BadRequest("Message is too long")
        return MagicMock()
    return AsyncMock(side_effect=_send)


@pytest.mark.asyncio
async def test_long_answer_is_split_and_every_chunk_is_accepted(connector):
    """THE regression. Previously: 2 x 400 -> total silence."""
    assert utf16_len(LONG_ANSWER) > TG_LIMIT, "fixture must exceed the cap"
    connector.handler.handle = AsyncMock(return_value=MessageResponse(text=LONG_ANSWER))
    update, context = _make_update_context("who is taro narahara")
    update.message.reply_text = _strict_reply_text()

    await connector._on_message(update, context)  # must not raise

    calls = update.message.reply_text.call_args_list
    assert len(calls) >= 2, "a 7k answer must be split"
    for call in calls:
        assert utf16_len(call[0][0]) <= TG_LIMIT


@pytest.mark.asyncio
async def test_long_answer_content_is_not_lost(connector):
    connector.handler.handle = AsyncMock(return_value=MessageResponse(text=LONG_ANSWER))
    update, context = _make_update_context("who is taro narahara")
    update.message.reply_text = _strict_reply_text()

    await connector._on_message(update, context)

    joined = "".join(c[0][0] for c in update.message.reply_text.call_args_list)
    assert "Taro Narahara" in joined
    assert "Research paragraph 8" in joined, "the tail must survive — no silent truncation"
    assert "scholar.google.com" in joined


@pytest.mark.asyncio
async def test_footer_and_keyboard_ride_on_the_last_chunk_only(connector):
    connector.handler.handle = AsyncMock(
        return_value=MessageResponse(
            text=LONG_ANSWER, question_id=42, source_note="https://people.njit.edu/profile/narahara"
        )
    )
    update, context = _make_update_context("who is taro narahara")
    update.message.reply_text = _strict_reply_text()

    await connector._on_message(update, context)

    calls = update.message.reply_text.call_args_list
    assert len(calls) >= 2
    for call in calls[:-1]:
        assert call.kwargs.get("reply_markup") is None
        assert "GSA Gateway" not in call[0][0], "footer must not repeat on every chunk"
    assert calls[-1].kwargs.get("reply_markup") is not None, "buttons belong on the last message"
    assert "GSA Gateway" in calls[-1][0][0]


@pytest.mark.asyncio
async def test_short_answer_is_still_exactly_one_message_with_buttons(connector):
    """Pins the 99% case: no behavior change, and the existing tests that read the LAST
    call_args stay valid."""
    connector.handler.handle = AsyncMock(
        return_value=MessageResponse(text="Taro Narahara is an Associate Professor.", question_id=7)
    )
    update, context = _make_update_context("who is taro narahara")
    update.message.reply_text = _strict_reply_text()

    await connector._on_message(update, context)

    update.message.reply_text.assert_called_once()
    assert update.message.reply_text.call_args.kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_html_parse_failure_falls_back_to_plain_text_that_also_fits(connector):
    """D2: the old fallback re-sent the FULL oversized text and 400'd a second time."""
    seen = []

    async def _send(text, **kwargs):
        seen.append((text, kwargs.get("parse_mode")))
        if utf16_len(text) > TG_LIMIT:
            raise BadRequest("Message is too long")
        if kwargs.get("parse_mode") == "HTML":
            raise BadRequest("Can't parse entities")
        return MagicMock()

    connector.handler.handle = AsyncMock(return_value=MessageResponse(text=LONG_ANSWER))
    update, context = _make_update_context("who is taro narahara")
    update.message.reply_text = AsyncMock(side_effect=_send)

    await connector._on_message(update, context)  # must not raise

    plain = [t for t, mode in seen if mode is None]
    assert plain, "must fall back to plain text"
    for text, _mode in seen:
        assert utf16_len(text) <= TG_LIMIT, "the fallback must itself be length-safe"


@pytest.mark.asyncio
async def test_retry_after_is_retried_so_the_tail_is_not_dropped(connector):
    """Without this, chunk 1 lands and chunk 2 vanishes — a silent partial answer."""
    state = {"raised": False}

    async def _send(text, **kwargs):
        if utf16_len(text) > TG_LIMIT:
            raise BadRequest("Message is too long")
        if not state["raised"] and text.startswith("Research paragraph"):
            state["raised"] = True
            raise RetryAfter(0.01)
        return MagicMock()

    connector.handler.handle = AsyncMock(return_value=MessageResponse(text=LONG_ANSWER))
    update, context = _make_update_context("who is taro narahara")
    update.message.reply_text = AsyncMock(side_effect=_send)

    await connector._on_message(update, context)

    joined = "".join(c[0][0] for c in update.message.reply_text.call_args_list)
    assert "Research paragraph 8" in joined, "tail dropped after RetryAfter"
