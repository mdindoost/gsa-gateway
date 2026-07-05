"""_compose_preserves_facts must reject a compose that DROPS an (affiliated)/(joint appointment)
marker — the home-vs-affiliated distinction is load-bearing and must not be reworded away. Runs for
ALL facts (incl. entity cards, which never match the counted-roster gate). Count-aware (Fable)."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bot.core.message_handler import _compose_preserves_facts

_CARD = ("Guiling Wang\nDistinguished Professor — Computer Science\n"
         "Distinguished Professor — Martin Tuchman School of Management (MTSM) (affiliated)\n"
         "Email: gwang@njit.edu")


def test_marker_dropped_rejected():
    composed = ("Hi there! Guiling Wang is a Distinguished Professor in Computer Science and at "
                "Martin Tuchman School of Management. Email: gwang@njit.edu")   # (affiliated) dropped
    assert _compose_preserves_facts(_CARD, composed) is False


def test_marker_preserved_accepted():
    composed = ("Hi there! Guiling Wang is a Distinguished Professor in Computer Science, with an "
                "affiliated appointment at MTSM (affiliated). Email: gwang@njit.edu")
    assert _compose_preserves_facts(_CARD, composed) is True


def test_count_aware_one_of_two_dropped_rejected():
    facts = ("Pat Doe\nProfessor — A (joint appointment)\nProfessor — B (joint appointment)")
    composed = "Pat Doe is a Professor at A (joint appointment) and B."   # second marker dropped
    assert _compose_preserves_facts(facts, composed) is False


def test_no_marker_facts_unaffected():
    # a plain roster with no markers still flows through the existing roster logic (no false reject)
    facts = "Computer Science has 2 faculty: Ann Lee, Bob Ng."
    composed = "Computer Science has 2 faculty: Ann Lee and Bob Ng."
    assert _compose_preserves_facts(facts, composed) is True
