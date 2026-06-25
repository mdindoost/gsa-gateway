import subprocess
from pathlib import Path


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


def test_no_headsup_references_in_repo():
    """Catch any file that imports or references the deleted bot.core.headsup module."""
    this_file = Path(__file__).resolve()
    root = Path(__file__).resolve().parents[2]  # repo root
    patterns = ["bot.core.headsup", "apply_headsup", "match_topic", "headsup_line"]

    matches = []
    for py_file in root.rglob("*.py"):
        if py_file.resolve() == this_file:
            continue
        try:
            src = py_file.read_text(errors="replace")
        except OSError:
            continue
        for pat in patterns:
            if pat in src:
                matches.append(f"{py_file.relative_to(root)}:{pat}")

    assert not matches, (
        "Dead headsup references found (B1 regression) — remove them:\n"
        + "\n".join(matches)
    )
