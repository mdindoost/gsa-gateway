from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from v2.core.ingestion.roster import project_roster
from scripts.verify_kg import verify_gsa


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.commit()
    yield c
    c.close()


def test_verify_gsa_flags_missing_officers_and_leftover_qa(conn):
    # no officers yet, and an active GSA faq item present -> two problems
    from v2.core.graph.orgs import ensure_org
    gid = ensure_org(conn, "gsa", "GSA", parent_slug="njit", type="custom")
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content) VALUES(?,'faq','q','a')", (gid,))
    conn.commit()
    issues = verify_gsa(conn)
    assert any("no GSA officers" in i for i in issues)
    assert any("active GSA QA" in i for i in issues)


def test_verify_gsa_passes_after_seed(conn):
    project_roster(conn, {"org": {"slug": "gsa", "name": "GSA", "parent": "njit"},
                          "people": [{"name": "Fernando", "title": "President", "category": "officer"}]})
    conn.commit()
    assert verify_gsa(conn) == []
