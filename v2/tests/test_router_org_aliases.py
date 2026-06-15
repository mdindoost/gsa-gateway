from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from v2.core.retrieval.router import route


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    # GWICS-style org: parenthetical acronym in the name + an admin alias in metadata
    # real-world ugly auto-slug (does NOT contain 'gwics'), so the acronym must come
    # from the parenthetical in the NAME, not the slug.
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type,metadata) "
              "VALUES(2,1,'Graduate Women in Computing Society (GWICS)',"
              "'graduate-women-in-computing-society-gwic','club',?)",
              ('{"aliases": ["women in computing"]}',))
    c.commit()
    yield c
    c.close()


def test_acronym_in_name_resolves(conn):
    # "(GWICS)" in the org name -> the acronym resolves the org
    r = route(conn, "who are the GWICS officers")
    assert r is not None and r.skill == "officers_in_org" and r.args["org_id"] == 2


def test_parenthetical_stripped_name_resolves(conn):
    # the clean name (without the "(GWICS)") resolves too
    r = route(conn, "who works at graduate women in computing society")
    assert r is not None and r.skill == "people_in_org" and r.args["org_id"] == 2


def test_metadata_alias_resolves(conn):
    # an admin-declared alias in organizations.metadata.aliases resolves
    r = route(conn, "who works at women in computing")
    assert r is not None and r.skill == "people_in_org" and r.args["org_id"] == 2


def test_short_token_does_not_false_match(conn):
    # a generic question with no org cue still returns None (no spurious org match)
    assert route(conn, "what is the weather today") is None
