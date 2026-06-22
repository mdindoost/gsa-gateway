import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.ingestion.office_quality import is_low_quality, dedup_boilerplate


def test_low_quality_drops_short_and_navlike():
    assert is_low_quality("Home About Contact Apply Visit")           # too few words
    assert is_low_quality("")                                          # empty
    assert not is_low_quality(
        "Visitor parking is available in the Lock Street Deck. " * 8)  # real prose


def test_dedup_removes_shared_nav_lines():
    nav = "Home\nDirectory\nApply Now"
    pages = [("u1", nav + "\nParking permits cost $X per year."),
             ("u2", nav + "\nThe mailroom is in Campus Center 220.")]
    out = dict(dedup_boilerplate(pages))
    assert "Apply Now" not in out["u1"]            # repeated nav line removed
    assert "Parking permits cost $X per year." in out["u1"]   # unique content kept
    assert "The mailroom is in Campus Center 220." in out["u2"]
