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
