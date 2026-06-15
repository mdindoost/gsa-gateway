from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.entry_points import child_for


def test_child_for_matches_computer_science():
    cp = child_for("Computer Science", "https://cs.njit.edu/faculty")
    assert cp is not None
    assert cp.kind == "listing"
    assert cp.org_slug == "computer-science"
    assert cp.parent_slug == "ywcc"
    assert cp.url == "https://cs.njit.edu/faculty"


def test_child_for_returns_none_for_unknown():
    assert child_for("About", "/about") is None
