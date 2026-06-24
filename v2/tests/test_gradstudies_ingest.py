import json

from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion import gradstudies_crawl as gc


def _db():
    conn = create_all(":memory:")
    ensure_org(conn, "njit", "New Jersey Institute of Technology", None, "university")
    # pre-create the existing graduate-studies org (id-9 analog) to exercise the REUSE path
    ensure_org(conn, "graduate-studies", "Graduate Studies", "njit", "office")
    return conn


def test_ingest_writes_staff_and_policy_prose_idempotently():
    conn = _db()
    res = gc.EntryResult(
        seed="https://www.njit.edu/graduatestudies/",
        staff=[gc.StaffRecord("Ester Flaim", "Assistant Director of Graduate Studies",
                              "973-596-8139", "ester.flaim@njit.edu", "")],
        prose=[gc.ProsePage("PhD Credit Requirements", "Verbatim policy text.",
                            "https://www.njit.edu/graduatestudies/content/new-phd-credit-requirements")],
        skipped=[])
    s1 = gc.ingest_gradstudies(conn, res)
    conn.commit()
    assert s1["staff"] == 1 and s1["prose_inserted"] == 1
    row = conn.execute("SELECT type, created_by FROM knowledge_items WHERE is_active=1").fetchone()
    assert tuple(row) == ("policy", "crawler")
    # staff contact persisted
    attrs = json.loads(conn.execute(
        "SELECT attrs FROM nodes WHERE type='Person' AND name='Ester Flaim'").fetchone()[0])
    assert attrs["email"] == "ester.flaim@njit.edu" and attrs["phone"] == "973-596-8139"
    # re-ingest unchanged → no new row
    s2 = gc.ingest_gradstudies(conn, res)
    conn.commit()
    assert s2["prose_unchanged"] == 1 and s2["prose_inserted"] == 0
    assert conn.execute("SELECT count(*) FROM knowledge_items WHERE is_active=1").fetchone()[0] == 1
    # org reused — exactly one graduate-studies org (no recreate)
    assert conn.execute(
        "SELECT count(*) FROM organizations WHERE slug='graduate-studies'").fetchone()[0] == 1
