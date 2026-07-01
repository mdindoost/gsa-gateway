"""Tests for the pure helpers of the serving/rank-preservation probe (Task 9)."""
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.prose_serving_probe import first_rank, count_titled


def test_first_rank_finds_token_position():
    titles_contents = [("A", "nothing here"), ("B", "the University Admissions office"),
                       ("C", "also University Admissions")]
    assert first_rank(titles_contents, "University Admissions") == 2   # 1-indexed rank


def test_first_rank_absent_is_none():
    assert first_rank([("A", "x"), ("B", "y")], "University Admissions") is None


def test_count_titled_counts_matches():
    titles = ["Graduate Admissions", "Office of Graduate Studies", "Graduate Admissions"]
    assert count_titled(titles, "Graduate Admissions") == 2
    assert count_titled(titles, "Admissions") == 0                    # exact title, not substring
