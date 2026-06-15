from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from scripts.gsa_retire_qa import retire_gsa_qa


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(2,1,'GSA','gsa','custom')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(3,1,'MMI','mmi','custom')")
    for t in ("faq", "faq"):
        c.execute("INSERT INTO knowledge_items(org_id,type,title,content) VALUES(2,?, 'q','a')", (t,))
    c.execute("INSERT INTO knowledge_items(org_id,type,title,content) VALUES(3,'faq','m','a')")
    c.execute("INSERT INTO knowledge_items(org_id,type,title,content) VALUES(2,'policy','p','a')")
    c.commit()
    yield c
    c.close()


def test_retire_gsa_qa_only_touches_gsa_faq(conn):
    n = retire_gsa_qa(conn)
    assert n == 2
    assert conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE org_id=2 AND type='faq' "
                        "AND is_active=1").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE org_id=3 AND type='faq' "
                        "AND is_active=1").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE org_id=2 AND type='policy' "
                        "AND is_active=1").fetchone()[0] == 1
