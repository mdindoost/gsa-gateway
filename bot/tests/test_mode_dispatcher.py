"""Tests for ModeDispatcher — explicit mode ownership (no implicit 'judging first')."""
import pytest

from bot.core.modes import ConversationModeStore, Mode, ModeDispatcher, ModeRegistry


class _FakeJudging:
    """Stand-in for JudgingSessionManager with controllable state + recording."""
    def __init__(self):
        self.modes = {}            # uid -> Mode|None  (what mode_of returns)
        self.triggers = {"judge mode", "presenter mode", "audience mode"}
        self.handle_calls = []
        # uid -> (response_text, consumed) to return from handle()
        self.handle_result = {}

    def mode_of(self, uid):
        return self.modes.get(uid)

    def is_trigger(self, text):
        return text.strip().lower() in self.triggers

    def handle(self, uid, text):
        self.handle_calls.append((uid, text))
        return self.handle_result.get(uid, ("judging-reply", True))


@pytest.fixture
def setup():
    store = ConversationModeStore()
    judging = _FakeJudging()
    registry = ModeRegistry(store, judging=judging)
    conv_calls = []

    async def conversation_handler(req):
        conv_calls.append(req)
        return f"conv-reply:{req['text']}"

    dispatcher = ModeDispatcher(
        registry, judging=judging, conversation_handler=conversation_handler
    )

    def make_request(uid, text):
        return {"user_id": uid, "text": text}

    return store, judging, dispatcher, conv_calls, make_request


# (a) gsa user types "judge mode" -> judging owns it (entry trigger)
@pytest.mark.asyncio
async def test_gsa_user_judge_trigger_routes_to_judging(setup):
    store, judging, dispatcher, conv_calls, mk = setup
    reply = await dispatcher.dispatch("u1", "judge mode", make_request=mk)
    assert judging.handle_calls == [("u1", "judge mode")]
    assert conv_calls == []
    assert reply.is_judging
    assert reply.text == "judging-reply"


# (b) judge mid-scoring types a normal question -> judging owns it (already in judging mode)
@pytest.mark.asyncio
async def test_judge_midscoring_normal_text_routes_to_judging(setup):
    store, judging, dispatcher, conv_calls, mk = setup
    judging.modes["u1"] = Mode.JUDGE        # already judging
    await dispatcher.dispatch("u1", "what is the travel award?", make_request=mk)
    assert judging.handle_calls == [("u1", "what is the travel award?")]
    assert conv_calls == []


# (e) free-mode user types "judge mode" -> judging owns it (trigger beats free mode)
@pytest.mark.asyncio
async def test_free_user_judge_trigger_routes_to_judging(setup):
    store, judging, dispatcher, conv_calls, mk = setup
    store.set("u1", Mode.FREE)
    await dispatcher.dispatch("u1", "judge mode", make_request=mk)
    assert judging.handle_calls == [("u1", "judge mode")]
    assert conv_calls == []


# gsa user normal question -> conversation handler (judging not consulted as owner)
@pytest.mark.asyncio
async def test_gsa_normal_question_routes_to_conversation(setup):
    store, judging, dispatcher, conv_calls, mk = setup
    reply = await dispatcher.dispatch("u1", "who are the GSA officers?", make_request=mk)
    assert judging.handle_calls == []
    assert conv_calls == [{"user_id": "u1", "text": "who are the GSA officers?"}]
    assert not reply.is_judging
    assert reply.text == "conv-reply:who are the GSA officers?"


# free-mode normal question -> conversation handler
@pytest.mark.asyncio
async def test_free_normal_question_routes_to_conversation(setup):
    store, judging, dispatcher, conv_calls, mk = setup
    store.set("u1", Mode.FREE)
    await dispatcher.dispatch("u1", "what is the capital of France?", make_request=mk)
    assert judging.handle_calls == []
    assert len(conv_calls) == 1


# (f) someone in judge mode types "free mode" -> judging owns it (still in judging mode),
#     judging machine itself decides what to do (here: stays/consumes). This preserves the
#     current behavior where judging intercepts while a session is active.
@pytest.mark.asyncio
async def test_judge_typing_free_mode_stays_with_judging(setup):
    store, judging, dispatcher, conv_calls, mk = setup
    judging.modes["u1"] = Mode.JUDGE
    await dispatcher.dispatch("u1", "free mode", make_request=mk)
    assert judging.handle_calls == [("u1", "free mode")]
    assert conv_calls == []


# Defensive: judging_owns but handle returns consumed=False -> fall through to conversation.
@pytest.mark.asyncio
async def test_judging_not_consumed_falls_through(setup):
    store, judging, dispatcher, conv_calls, mk = setup
    judging.modes["u1"] = Mode.JUDGE
    judging.handle_result["u1"] = (None, False)
    await dispatcher.dispatch("u1", "anything", make_request=mk)
    assert judging.handle_calls == [("u1", "anything")]
    assert conv_calls == [{"user_id": "u1", "text": "anything"}]


# No judging wired (Discord-style) -> always conversation.
@pytest.mark.asyncio
async def test_no_judging_always_conversation():
    store = ConversationModeStore()
    registry = ModeRegistry(store, judging=None)
    conv_calls = []

    async def conv(req):
        conv_calls.append(req)
        return "ok"

    dispatcher = ModeDispatcher(registry, judging=None, conversation_handler=conv)
    await dispatcher.dispatch("u1", "judge mode", make_request=lambda u, t: {"u": u, "t": t})
    assert conv_calls == [{"u": "u1", "t": "judge mode"}]
