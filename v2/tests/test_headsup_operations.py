"""The EOS/parking heads-up: volatile facts (permit fees, hours, lockout numbers) get a
'confirm with the parking office' line — the staleness mitigation for the KB snapshot."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bot.core.headsup import apply_headsup, match_topic


def test_parking_questions_match_operations_topic():
    for q in ["how much is a parking permit", "where do I park", "campus shuttle",
              "who do I call for a lockout", "where is the mailroom", "visitor parking",
              "how do I get my photo id"]:
        t = match_topic(q)
        assert t is not None and t.name == "operations", q


def test_operations_headsup_appended_with_office():
    out = apply_headsup("Permits are $X.", "how much is a parking permit")
    assert "Permits are $X." in out
    assert "confirm" in out.lower() and "parking" in out.lower()


def test_unrelated_question_gets_no_operations_headsup():
    assert match_topic("who is the dean of YWCC") is None
