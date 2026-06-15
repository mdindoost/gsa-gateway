from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from v2.core.ingestion.roster import project_roster
from v2.core.retrieval.router import route
from v2.core.retrieval import structured_answer


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    project_roster(c, {"org": {"slug": "gsa", "name": "Graduate Student Association", "parent": "njit"},
                       "people": [
                           {"name": "Fernando Vera Buschmann", "title": "GSA President", "category": "officer"},
                           {"name": "Mohith Oduru", "title": "VP Finances", "category": "officer"}]})
    c.commit()
    yield c
    c.close()


def test_officers_route_runs_and_formats(conn):
    rt = route(conn, "who are the GSA officers?")
    assert rt and rt.skill == "officers_in_org"
    ans = structured_answer.format_answer(structured_answer.run(conn, rt))
    assert "Mohith Oduru" in ans and "VP Finances" in ans
    assert "Fernando Vera Buschmann" in ans
    assert "2 officer" in ans


def test_officers_empty_is_stated_not_guessed(conn):
    # GSA exists but query an org with no officers (NJIT id=1)
    from v2.core.retrieval.router import Route
    ans = structured_answer.format_answer(
        structured_answer.run(conn, Route("officers_in_org", {"org_id": 1})))
    assert "don't have officer" in ans.lower()
