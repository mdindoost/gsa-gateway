import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from scripts.ingest_offices import ingest_one_office, LEGACY_SEED


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(2,1,'Graduate Student Association','gsa','custom')")
    # a legacy GSA-filed seed contact for OGI (id 125 mirrors production)
    c.execute("INSERT INTO knowledge_items(id,org_id,type,title,content,is_active,created_by) "
              "VALUES(125,2,'contact','Office of Global Initiatives (OGI)','old seed',1,'migration')")
    c.commit()
    yield c
    c.close()


def test_ingest_creates_office_org_and_contact_doc(conn):
    n = ingest_one_office(conn, slug="bursar", name="Office of the Bursar",
                          parent="njit", title="Office of the Bursar",
                          source_url="https://www.njit.edu/bursar",
                          body="Handles: tuition, billing, payments.\n\nEmail: studentaccounts@njit.edu")
    assert n >= 1
    org = conn.execute("SELECT id,type FROM organizations WHERE slug='bursar'").fetchone()
    assert org is not None and org["type"] == "office"
    active = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND "
        "json_extract(metadata,'$.doc_id')='gsa-doc/bursar'").fetchone()[0]
    assert active == n


def test_legacy_seed_is_retired_not_duplicated(conn):
    # OGI is in LEGACY_SEED (id 125) — ingesting it must deactivate the old GSA-filed contact.
    ingest_one_office(conn, slug="ogi", name="Office of Global Initiatives",
                      parent="njit", title="Office of Global Initiatives (OGI)",
                      source_url="https://www.njit.edu/global",
                      body="Handles: visa, I-20, CPT, OPT.\n\nEmail: ogi@njit.edu")
    old = conn.execute("SELECT is_active FROM knowledge_items WHERE id=125").fetchone()["is_active"]
    assert old == 0  # retired
    active_ogi = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND type='contact' "
        "AND content LIKE '%visa%'").fetchone()[0]
    assert active_ogi >= 1  # the new one is active; no duplicate of the old
    assert LEGACY_SEED["ogi"] == 125
