import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.repair_paren_fragmented_areas import has_unbalanced, rederive_areas


def test_has_unbalanced_detects_fragmented_parens():
    assert has_unbalanced(["Machine Learning (Statistical Learning", "Kernel Methods)"])
    assert has_unbalanced(["graph learning (e.g", "etc.)"])


def test_has_unbalanced_ignores_balanced_areas():
    assert not has_unbalanced(["Video Analytics", "Pattern Recognition (Face/Iris)"])
    assert not has_unbalanced(["Algorithms", "Machine Learning"])


def test_rederive_regroups_parenthetical_from_content():
    content = ("Research areas of Chengjun Liu (Computer Science): Video Analytics; "
               "Machine Learning (Statistical Learning; Kernel Methods; Similarity Measures); "
               "Computer Vision (Object/Face Detection; Video Processing)")
    assert rederive_areas(content) == [
        "Video Analytics",
        "Machine Learning (Statistical Learning; Kernel Methods; Similarity Measures)",
        "Computer Vision (Object/Face Detection; Video Processing)"]
