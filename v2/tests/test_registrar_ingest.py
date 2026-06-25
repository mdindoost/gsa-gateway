from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion import registrar_crawl as rc


def _db():
    conn = create_all(":memory:")
    ensure_org(conn, "njit", "New Jersey Institute of Technology", None, "university")
    # pre-create the existing registrar org (id-24 analog) to exercise the REUSE path
    ensure_org(conn, "registrar", "Office of the Registrar", "njit", "office")
    return conn


def _res():
    return rc.EntryResult(
        seed="https://www.njit.edu/registrar/",
        staff=[rc.StaffRecord(name="Jerry Trombella", title="University Registrar",
                              phone="973-596-3236", email="jerry.trombella@njit.edu",
                              unit="Registrar Staff", titles=("University Registrar",))],
        prose=[rc.ProsePage("Withdrawal", "Verbatim withdrawal policy text.",
                            "https://www.njit.edu/registrar/withdrawal")],
        skipped=[])


def test_ingest_writes_staff_and_policy_prose_idempotently():
    conn = _db()
    s1 = rc.ingest_registrar(conn, _res())
    conn.commit()
    assert s1["staff"] == 1 and s1["prose_inserted"] == 1
    row = conn.execute("SELECT type, created_by FROM knowledge_items WHERE is_active=1").fetchone()
    assert tuple(row) == ("policy", "crawler")
    # one Person, with phone attr and NO email key (the roster carries no email)
    import json
    pid, attrs = conn.execute(
        "SELECT id, attrs FROM nodes WHERE type='Person' AND key='crawler/registrar/jerry-trombella'"
    ).fetchone()
    a = json.loads(attrs)
    assert a.get("phone") == "973-596-3236"
    assert a.get("email") == "jerry.trombella@njit.edu"   # captured personal address is stored
    # re-ingest unchanged → no new prose row, no duplicate person/org
    s2 = rc.ingest_registrar(conn, _res())
    conn.commit()
    assert s2["prose_unchanged"] == 1 and s2["prose_inserted"] == 0
    assert conn.execute("SELECT count(*) FROM knowledge_items WHERE is_active=1").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM nodes WHERE type='Person'").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM organizations WHERE slug='registrar'").fetchone()[0] == 1
