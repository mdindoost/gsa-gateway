"""Follow-up: the is_officer_query GSA-framing hack is removed (2026-07-03).

It matched 6 hardcoded GSA officer FIRST names and rewrote the query to "Who is {Name} at GSA NJIT?
Contact…" with a 'contact' source filter — a GSA/owner-privileging hack (hardcoded mohammad->Dindoost
despite 4 Mohammads) that a live measurement showed did not even resolve the officers. Removal is
neutral on surfacing, strictly better on GSA-equal. Behavioral guarantee is in test_message_handler.py.
"""
from __future__ import annotations

import bot.core.message_handler as mh


def test_officer_first_names_constant_removed():
    assert not hasattr(mh, "_OFFICER_FIRST_NAMES"), \
        "the hardcoded GSA officer first-name set must be deleted, not just unused"


def test_no_hardcoded_gsa_officer_reframe_text():
    import inspect
    src = inspect.getsource(mh)
    assert "Contact information and role for" not in src, \
        "the hardcoded GSA-contact reframe string must be gone"
