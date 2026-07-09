from bot.services.ollama_client import BASE_SYSTEM_PROMPT


def test_base_prompt_has_time_qualifier_guard():
    p = BASE_SYSTEM_PROMPT.lower()
    assert "time or schedule qualifier" in p
    assert "next semester" in p
    # must instruct NOT to assert an unconfirmed qualifier
    assert "do not assert" in p
