import json
import pytest
from autoeval.codex_client import decide, detect_rate_limit, RateLimitError, extract_error_message


def _agent_msg(payload):
    return json.dumps({"type": "item.completed",
                       "item": {"type": "agent_message", "text": json.dumps(payload)}})


def test_decide_parses_agent_message():
    out = _agent_msg({"questions": []})
    assert decide(out, "", 0) == {"questions": []}


def test_decide_raises_ratelimit_from_structured_error():
    err_event = json.dumps({"type": "error",
                            "message": "You've hit your usage limit. try again at 10:06 PM."})
    with pytest.raises(RateLimitError):
        decide(err_event, "", 1)


def test_extract_error_message_reads_turn_failed():
    line = json.dumps({"type": "turn.failed", "error": {"message": "usage limit; resets 3:00 AM"}})
    assert "resets" in extract_error_message(line)
