from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion import bursar_crawl as bc


def _db():
    conn = create_all(":memory:")
    ensure_org(conn, "njit", "New Jersey Institute of Technology", None, "university")
    # pre-create the existing bursar org (id-17 analog) to exercise the REUSE path
    ensure_org(conn, "bursar", "Office of the Bursar / Student Accounts", "njit", "office")
    return conn


def test_ingest_writes_policy_prose_idempotently_zero_people():
    conn = _db()
    res = bc.EntryResult(
        seed="https://www.njit.edu/bursar/",
        staff=[],                                          # prose-only office
        prose=[bc.ProsePage("eRefund", "Verbatim eRefund setup text.",
                            "https://www.njit.edu/bursar/touchnet-erefund")],
        skipped=[])
    s1 = bc.ingest_bursar(conn, res)
    conn.commit()
    assert s1["staff"] == 0 and s1["prose_inserted"] == 1
    row = conn.execute("SELECT type, created_by FROM knowledge_items WHERE is_active=1").fetchone()
    assert tuple(row) == ("policy", "crawler")
    assert conn.execute("SELECT count(*) FROM nodes WHERE type='Person'").fetchone()[0] == 0
    # re-ingest unchanged → no new row
    s2 = bc.ingest_bursar(conn, res)
    conn.commit()
    assert s2["prose_unchanged"] == 1 and s2["prose_inserted"] == 0
    assert conn.execute("SELECT count(*) FROM knowledge_items WHERE is_active=1").fetchone()[0] == 1
    # org reused — exactly one bursar org (no recreate)
    assert conn.execute("SELECT count(*) FROM organizations WHERE slug='bursar'").fetchone()[0] == 1
