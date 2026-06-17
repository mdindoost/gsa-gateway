import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import get_connection
from v2.core.retrieval.router import route


@pytest.fixture(scope="module")
def conn():
    return get_connection("gsa_gateway.db")


@pytest.mark.parametrize("q", [
    "who are the GSA officers",
    "who is the GSA president",
    "who's the GSA VP of Finance",
    "list the GSA officers",
])
def test_identity_questions_route_to_officers(conn, q):
    r = route(conn, q)
    assert r is not None and r.skill == "officers_in_org"


@pytest.mark.parametrize("q", [
    "who can impeach a GSA officer and what vote is needed",
    "what are the duties of the VP of Finance",
    "how do I become a GSA officer",
    "who is eligible to be an officer",
    "how many officers does the GSA have",
])
def test_process_questions_fall_through_to_rag(conn, q):
    r = route(conn, q)
    assert r is None or r.skill != "officers_in_org"
