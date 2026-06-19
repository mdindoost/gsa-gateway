from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import json

from v2.core.database.schema import create_all
from v2.core.ingestion.entry_points import apply_org_aliases, child_for


def test_child_for_matches_computer_science():
    cp = child_for("Computer Science", "https://cs.njit.edu/faculty")
    assert cp is not None
    assert cp.kind == "listing"
    assert cp.org_slug == "computer-science"
    assert cp.parent_slug == "ywcc"
    assert cp.url == "https://cs.njit.edu/faculty"


def test_child_for_returns_none_for_unknown():
    assert child_for("About", "/about") is None


def _aliases(conn, slug):
    m = conn.execute("SELECT metadata FROM organizations WHERE slug=?", (slug,)).fetchone()[0]
    return (json.loads(m) if m else {}).get("aliases") or []


def test_apply_org_aliases_sets_common_names_and_preserves_other_metadata():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(name,slug,type,metadata) "
              "VALUES('Civil & Environmental Engineering','civil-environmental-engineering',"
              "'department',?)", (json.dumps({"org_id": 9}),))
    c.commit()
    apply_org_aliases(c)
    al = _aliases(c, "civil-environmental-engineering")
    assert "civil engineering" in al                     # the short name users type now resolves
    # other metadata keys are preserved (merge, not clobber)
    meta = json.loads(c.execute("SELECT metadata FROM organizations WHERE "
                                "slug='civil-environmental-engineering'").fetchone()[0])
    assert meta["org_id"] == 9


def test_apply_org_aliases_idempotent():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(name,slug,type) VALUES('Physics','physics','department')")
    c.commit()
    apply_org_aliases(c)
    second = apply_org_aliases(c)                         # nothing new to write the 2nd time
    assert second == 0
    assert "physics" in _aliases(c, "physics")
