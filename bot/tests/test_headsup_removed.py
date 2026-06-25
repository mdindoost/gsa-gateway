def test_no_headsup_module():
    import importlib
    try:
        importlib.import_module("bot.core.headsup")
        raised = False
    except ModuleNotFoundError:
        raised = True
    assert raised, "bot.core.headsup should be deleted"


def test_message_handler_has_no_headsup_call():
    src = open("bot/core/message_handler.py").read()
    assert "apply_headsup" not in src
    assert "headsup" not in src
