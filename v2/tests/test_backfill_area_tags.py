import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.backfill_research_area_tags import area_tags_from_content


def test_recovers_list_from_joined_content():
    c = "Research areas of Vincent Oria (Computer Science): Multimedia Databases; Spatio-temporal Databases; Recommender Systems"
    assert area_tags_from_content(c) == [
        "Multimedia Databases", "Spatio-temporal Databases", "Recommender Systems"]


def test_single_area_content():
    c = "Research areas of X (CS): Algorithms"
    assert area_tags_from_content(c) == ["Algorithms"]


def test_no_colon_returns_empty():
    assert area_tags_from_content("garbage with no separator") == []


def test_semicolon_inside_parens_does_not_fragment():
    # a ';' inside parentheses is part of one area, not an area separator — re-running
    # backfill on already-grouped content must stay idempotent (not re-fragment).
    c = ("Research areas of Chengjun Liu (CS): Video Analytics; "
         "Machine Learning (Statistical Learning; Kernel Methods; Similarity Measures); "
         "Computer Vision")
    assert area_tags_from_content(c) == [
        "Video Analytics",
        "Machine Learning (Statistical Learning; Kernel Methods; Similarity Measures)",
        "Computer Vision"]
