"""TDD — Accuracy Quick-Wins Wave 2, QW-A4a: the compose survival check.

compose_from_rows truncates its output at num_predict; an uncapped roster ("X has 137 faculty: …")
can have the COUNT survive while the tail NAMES vanish — and _compose_structured accepted any non-empty
compose. This guard: for a COUNTED-ROSTER Facts, every email / 3+-digit run / list-item tail token in
Facts must survive in the composed answer, else the caller keeps Facts VERBATIM (rule #2/#4). Short
cards/prose skip the check (return True) so the friendly "Hi there!" greeting is never collapsed.

Design note (deviation from the spec's "; "-only assumption): the real roster skills use MIXED
separators — faculty_in_department / people_by_research_area use ", " (via _join); officers_in_org /
people_in_org use "; ". The check is separator-robust and errs toward verbatim-facts (safe) on doubt.
To be confirmed by Fable at diff review.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.core.message_handler import _compose_preserves_facts, MessageHandler


# ─────────────────────────────── the pure guard ───────────────────────────────
_COMMA_ROSTER = ('Computer Science has 6 faculty: Ajim Uddin, Alexandros Gerbessiotis, '
                 'Anirudh Sridhar, Dantong Yu, Ioannis Koutis, Kristina Wicke.')
_SEMI_ROSTER = ('GSA has 3 officer(s): President — Fernando Alba (fa@njit.edu); '
                'Treasurer — Mohith Rao (mr@njit.edu); Secretary — Nistha Shah (ns@njit.edu).')


def test_a4a_comma_roster_all_names_present_passes():
    composed = ("Computer Science has 6 faculty: Ajim Uddin, Alexandros Gerbessiotis, "
                "Anirudh Sridhar, Dantong Yu, Ioannis Koutis, and Kristina Wicke.")
    assert _compose_preserves_facts(_COMMA_ROSTER, composed) is True


def test_a4a_comma_roster_reordered_still_passes():
    composed = ("The CS faculty are Kristina Wicke, Ioannis Koutis, Dantong Yu, "
                "Anirudh Sridhar, Alexandros Gerbessiotis and Ajim Uddin.")
    assert _compose_preserves_facts(_COMMA_ROSTER, composed) is True


def test_a4a_comma_roster_truncated_tail_fails():
    """The classic bug: count survives, tail names dropped."""
    composed = "Computer Science has 6 faculty: Ajim Uddin, Alexandros Gerbessiotis, Anirudh Sridhar…"
    assert _compose_preserves_facts(_COMMA_ROSTER, composed) is False   # Yu/Koutis/Wicke missing


def test_a4a_semicolon_roster_dropped_email_fails():
    composed = ("GSA has 3 officers: President Fernando Alba (fa@njit.edu); "
                "Treasurer Mohith Rao (mr@njit.edu); Secretary Nistha Shah.")   # ns@njit.edu dropped
    assert _compose_preserves_facts(_SEMI_ROSTER, composed) is False


def test_a4a_dropped_phone_digits_fail():
    facts = "Ioannis Koutis — Email: ik@njit.edu; Phone: 973-596-1234."
    # not a counted roster → but has digits; make it a counted roster shape so the guard engages:
    facts = "Directory has 1 person: Ioannis Koutis — ik@njit.edu, 973-596-1234."
    composed = "Ioannis Koutis can be reached at ik@njit.edu."                  # phone dropped
    assert _compose_preserves_facts(facts, composed) is False


def test_a4a_short_card_skips_check():
    """A person card (no counted-roster lead-in) is NOT second-guessed — compose is trusted so the
    friendly greeting/phrasing survives, even though a token 'differs'."""
    facts = "Ioannis Koutis is a Professor in Computer Science. Email: ik@njit.edu."
    composed = "Hi there! Ioannis Koutis is a professor in the CS department."   # email not restated
    assert _compose_preserves_facts(facts, composed) is True                     # skipped → trusts compose


def test_a4a_semicolon_roster_dropped_name_fails():
    """A dropped NAME (not just email) in a semicolon officer roster is caught by the tail-token check."""
    composed = ("GSA has 3 officers: President — Fernando Alba (fa@njit.edu); "
                "Treasurer — Mohith Rao (mr@njit.edu).")               # Secretary Nistha Shah dropped
    assert _compose_preserves_facts(_SEMI_ROSTER, composed) is False


def test_a4a_word_boundary_no_false_pass_on_substring_surname():
    """Fable note #2: a dropped 'Chen' must NOT be masked by a surviving 'Cheng'."""
    facts = "Lab has 2 people: Wei Chen, Ming Cheng."
    composed = "The lab includes Ming Cheng."                          # 'Chen' dropped; 'Cheng' present
    assert _compose_preserves_facts(facts, composed) is False


def test_a4a_faculty_areas_skips_check_pin():
    """PIN (declared gap): faculty_areas_in_department's 'N of the {org} faculty list research areas'
    lead-in does NOT match the counted-roster pattern → the guard SKIPS it (so a legitimate AREA
    paraphrase can't false-strip the compose). Records the decision so widening it is deliberate."""
    facts = ("3 of the Computer Science faculty list research areas: "
             "Koutis — Spectral graph theory; Wicke — Graph theory; Xu — Machine learning.")
    composed = "Three CS faculty list areas: Koutis works on spectral methods."   # heavily paraphrased
    assert _compose_preserves_facts(facts, composed) is True           # skipped → compose trusted


def test_a4a_diacritics_normalized():
    facts = "Math has 2 faculty: José Fernández, Zoë Müller."
    composed = "The math faculty are Jose Fernandez and Zoe Muller."             # ASCII-folded
    assert _compose_preserves_facts(facts, composed) is True


# ─────────────────────────────── integration on _compose_structured ───────────
def _handler(compose_return):
    ollama = AsyncMock()
    ollama.compose_from_rows = AsyncMock(return_value=compose_return)
    return MessageHandler(retriever=AsyncMock(), ollama=ollama, conversation_manager=MagicMock(),
                          intent_detector=MagicMock(), db=MagicMock(), rate_limiter=MagicMock(),
                          kb=MagicMock(), config=MagicMock())


@pytest.mark.asyncio
async def test_a4a_compose_structured_falls_back_on_drop():
    """When compose drops roster names, _compose_structured serves the complete Facts verbatim."""
    truncated = "Computer Science has 6 faculty: Ajim Uddin, Alexandros Gerbessiotis…"
    h = _handler(truncated)
    out = await h._compose_structured("who are CS faculty", _COMMA_ROSTER, suffix="", deterministic=False)
    assert out == _COMMA_ROSTER                          # verbatim facts, not the truncated compose


@pytest.mark.asyncio
async def test_a4a_compose_structured_uses_good_compose():
    good = ("Computer Science has 6 faculty: Ajim Uddin, Alexandros Gerbessiotis, Anirudh Sridhar, "
            "Dantong Yu, Ioannis Koutis, and Kristina Wicke.")
    h = _handler(good)
    out = await h._compose_structured("who are CS faculty", _COMMA_ROSTER, suffix="", deterministic=False)
    assert out == good


@pytest.mark.asyncio
async def test_a4a_suffix_appended_after_check():
    """The deterministic suffix is not part of Facts and must not affect the check; it is still appended."""
    good = ("Computer Science has 6 faculty: Ajim Uddin, Alexandros Gerbessiotis, Anirudh Sridhar, "
            "Dantong Yu, Ioannis Koutis, and Kristina Wicke.")
    h = _handler(good)
    out = await h._compose_structured("who are CS faculty", _COMMA_ROSTER,
                                      suffix="🔗 Profiles: …", deterministic=False)
    assert out.endswith("🔗 Profiles: …")
    assert "Kristina Wicke" in out
