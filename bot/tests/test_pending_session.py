from datetime import datetime, timezone
from bot.core.pending import PendingAction, PendingOption
from bot.services.conversation import ConversationManager


def _pa():
    return PendingAction(
        options=[PendingOption("most citations", "structured",
                               {"skill": "top_people_by_metric", "args": {"org_id": 5}})],
        created_at=datetime.now(timezone.utc),
    )


def test_set_get_clear_pending():
    cm = ConversationManager()
    assert cm.get_pending("u1") is None
    cm.set_pending("u1", _pa())
    got = cm.get_pending("u1")
    assert got is not None and got.options[0].payload["skill"] == "top_people_by_metric"
    cm.clear_pending("u1")
    assert cm.get_pending("u1") is None


def test_clear_session_drops_pending():
    cm = ConversationManager()
    cm.set_pending("u1", _pa())
    cm.clear_session("u1")
    assert cm.get_pending("u1") is None


def test_mode_switch_clears_session_and_pending():
    import bot.config as botcfg
    _prev = botcfg.FOLLOWUP_RESUME_ENABLED
    botcfg.FOLLOWUP_RESUME_ENABLED = True
    try:
        cm = ConversationManager()
        cm.add_turn("u1", "user", "who has the lowest citation in ywcc")
        cm.set_pending("u1", _pa())
        cm.set_mode("u1", "free")                       # actual change gsa -> free
        assert cm.get_pending("u1") is None             # pending wiped
        assert cm.get_history("u1") == []               # context wiped
        assert cm.get_mode("u1") == "free"              # new mode stuck (not reset to gsa)
    finally:
        botcfg.FOLLOWUP_RESUME_ENABLED = _prev


def test_mode_switch_flag_off_preserves_session():
    import bot.config as botcfg
    _prev = botcfg.FOLLOWUP_RESUME_ENABLED
    botcfg.FOLLOWUP_RESUME_ENABLED = False
    try:
        cm = ConversationManager()
        cm.add_turn("u1", "user", "hello")
        cm.set_mode("u1", "free")               # switch, but flag OFF
        assert cm.get_history("u1") != []       # history PRESERVED (pre-feature behavior)
        assert cm.get_mode("u1") == "free"      # mode still changed
    finally:
        botcfg.FOLLOWUP_RESUME_ENABLED = _prev


def test_mode_set_same_mode_is_noop():
    cm = ConversationManager()
    cm.add_turn("u1", "user", "hello")
    cm.set_mode("u1", "gsa")                         # unchanged (default is gsa)
    assert cm.get_history("u1") != []               # NOT wiped
