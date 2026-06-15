from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from v2.core.ingestion.roster import project_roster
from scripts._retire_officer_contacts import retire_officer_contacts


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    project_roster(c, {"org": {"slug": "gsa", "name": "GSA", "parent": "njit"},
                       "people": [{"name": "Mohith Oduru", "title": "VP Finances", "category": "officer"},
                                  {"name": "Fernando", "title": "GSA President", "category": "officer"}]})
    gsa = c.execute("SELECT id FROM organizations WHERE slug='gsa'").fetchone()[0]
    # contact cards: 2 officer duplicates + 1 campus office that must be kept
    for title in ("VP Finances", "GSA President", "Counseling Center (C-CAPS)"):
        c.execute("INSERT INTO knowledge_items(org_id,type,title,content) VALUES(?,'contact',?,?)",
                  (gsa, title, title))
    c.commit()
    yield c
    c.close()


def test_retires_only_officer_cards(conn):
    retired = retire_officer_contacts(conn)
    assert set(retired) == {"VP Finances", "GSA President"}
    gsa = conn.execute("SELECT id FROM organizations WHERE slug='gsa'").fetchone()[0]
    active = [r[0] for r in conn.execute(
        "SELECT title FROM knowledge_items WHERE org_id=? AND type='contact' AND is_active=1",
        (gsa,))]
    assert active == ["Counseling Center (C-CAPS)"]   # campus office kept


def test_no_officers_means_no_retirement(conn):
    # if the KG has no officers, nothing is matched/retired (safety)
    conn.execute("UPDATE edges SET is_active=0 WHERE type='has_role'")
    conn.commit()
    assert retire_officer_contacts(conn) == []
