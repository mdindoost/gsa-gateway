import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion.office_ingest import content_hash, ingest_office_page

URL = "https://www.njit.edu/parking/visitor-parking"
TEXT = "Visitor parking is available in the Lock Street Deck. " * 8


def _org(conn):
    with conn:
        return ensure_org(conn, slug="eos", name="EOS", parent_slug=None, type="office")


def test_content_hash_is_stable_and_text_sensitive():
    assert content_hash(TEXT) == content_hash(TEXT)
    assert content_hash(TEXT) != content_hash(TEXT + " Updated.")


def test_unchanged_page_is_skipped_on_reingest(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    oid = _org(conn)
    with conn:
        n1, leg1 = ingest_office_page(conn, org_id=oid, url=URL, title="Visitor Parking", text=TEXT)
    assert leg1 == "chunk" and n1 >= 1
    rows1 = conn.execute("SELECT COUNT(*) c FROM knowledge_items WHERE type='office_page'").fetchone()["c"]
    with conn:
        n2, leg2 = ingest_office_page(conn, org_id=oid, url=URL, title="Visitor Parking", text=TEXT)
    assert leg2 == "unchanged" and n2 == 0
    rows2 = conn.execute("SELECT COUNT(*) c FROM knowledge_items WHERE type='office_page'").fetchone()["c"]
    assert rows2 == rows1                              # no churn — same active rows


def test_changed_page_reingests_and_updates_hash(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    oid = _org(conn)
    with conn:
        ingest_office_page(conn, org_id=oid, url=URL, title="Visitor Parking", text=TEXT)
        n2, leg2 = ingest_office_page(conn, org_id=oid, url=URL, title="Visitor Parking",
                                      text=TEXT + " New permit info added.")
    assert leg2 == "chunk" and n2 >= 1
    h = conn.execute("SELECT content_hash FROM office_page_state WHERE url=?", (URL,)).fetchone()[0]
    assert h == content_hash(TEXT + " New permit info added.")
