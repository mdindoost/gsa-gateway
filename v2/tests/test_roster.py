from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from v2.core.ingestion.roster import project_roster, reconcile_roster
from v2.core.retrieval.skills import officers_in_org

ROSTER = {
    "org": {"slug": "gsa", "name": "Graduate Student Association", "parent": "njit"},
    "people": [
        {"name": "Fernando Vera Buschmann", "title": "GSA President", "category": "officer",
         "email": "gsa-pres@njit.edu", "note": "Data Science PhD"},
        {"name": "Mohith Oduru", "title": "VP Finances", "category": "officer",
         "email": "gsa-vpf@njit.edu"},
    ],
    "rgos": [
        {"slug": "phd-club", "name": "PhD Club",
         "people": [{"name": "Ana Lee", "title": "President", "category": "officer"}]},
    ],
}


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.commit()
    yield c
    c.close()


def test_project_roster_creates_officers_and_rgos(conn):
    keys = project_roster(conn, ROSTER)
    gsa = conn.execute("SELECT id FROM organizations WHERE slug='gsa'").fetchone()[0]
    phd = conn.execute("SELECT id FROM organizations WHERE slug='phd-club'").fetchone()[0]
    assert ("Fernando Vera Buschmann", "GSA President") in [(n, t) for n, t, _ in officers_in_org(conn, gsa)]
    assert ("Ana Lee", "President") in [(n, t) for n, t, _ in officers_in_org(conn, phd)]
    # email from the roster is carried through to the officer listing
    assert any(n == "Fernando Vera Buschmann" and e == "gsa-pres@njit.edu"
               for n, _t, e in officers_in_org(conn, gsa))
    assert conn.execute("SELECT parent_id FROM organizations WHERE id=?", (phd,)).fetchone()[0] == gsa
    assert any(k[1] == "dashboard/gsa/fernando-vera-buschmann" for k in keys)


def test_reconcile_roster_deactivates_departed_officer(conn):
    project_roster(conn, ROSTER)
    gsa = conn.execute("SELECT id FROM organizations WHERE slug='gsa'").fetchone()[0]
    smaller = dict(ROSTER, people=[ROSTER["people"][0]])
    present = project_roster(conn, smaller)
    removed = reconcile_roster(conn, present)
    names = [n for n, _t, _e in officers_in_org(conn, gsa)]
    assert "Mohith Oduru" not in names
    assert "Fernando Vera Buschmann" in names
    assert removed == 1


def test_project_roster_idempotent(conn):
    project_roster(conn, ROSTER)
    project_roster(conn, ROSTER)
    gsa = conn.execute("SELECT id FROM organizations WHERE slug='gsa'").fetchone()[0]
    assert len(officers_in_org(conn, gsa)) == 2
