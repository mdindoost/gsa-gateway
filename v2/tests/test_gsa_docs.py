from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from v2.core.ingestion.gsa_docs import chunk_doc, upsert_doc_items


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(2,1,'Graduate Student Association','gsa','custom')")
    c.commit()
    yield c
    c.close()


def test_chunk_doc_splits_long_text():
    chunks = chunk_doc("word " * 2000)
    assert len(chunks) >= 4
    assert all(c.strip() for c in chunks)


def test_upsert_doc_items_inserts_and_is_idempotent(conn):
    text = "GSA Travel Awards support graduate students presenting at conferences. " * 30
    n1 = upsert_doc_items(conn, org_id=2, slug="travel-award", title="GSA Travel Awards",
                          text=text, source_url="https://www.gsanjit.com/travel",
                          doc_type="policy")
    assert n1 >= 1
    active = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND "
        "json_extract(metadata,'$.doc_id')='gsa-doc/travel-award'").fetchone()[0]
    assert active == n1
    n2 = upsert_doc_items(conn, org_id=2, slug="travel-award", title="GSA Travel Awards",
                          text=text, source_url="https://www.gsanjit.com/travel",
                          doc_type="policy")
    active2 = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND "
        "json_extract(metadata,'$.doc_id')='gsa-doc/travel-award'").fetchone()[0]
    assert active2 == n2 and active2 == n1
