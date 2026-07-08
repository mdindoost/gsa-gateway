import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.types import XRay
from eval.processing_debt.pathlabel import classify_path


def _xr(*, skill=None, primary_miss=False):
    return XRay("q", None, skill, [1, 2], [1], {1: 0.5}, primary_miss, None)


def test_router_hit_when_skill_present():
    assert classify_path(_xr(skill="people_by_role")) == "router_hit"

def test_live_fallback_when_primary_miss():
    assert classify_path(_xr(skill=None, primary_miss=True)) == "live_fallback"

def test_rag_otherwise():
    assert classify_path(_xr(skill=None, primary_miss=False)) == "rag"
