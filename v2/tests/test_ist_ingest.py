from v2.core.database.schema import create_all, get_connection
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion import ist_crawl
from v2.core.ingestion.ist_crawl import EntryResult, StaffRecord, ProsePage


def _conn(tmp_path):
    db = str(tmp_path / "t.db")
    create_all(db)
    conn = get_connection(db)
    ensure_org(conn, "njit", "New Jersey Institute of Technology", type="university")
    ensure_org(conn, "ist", "IST / Technology Support", parent_slug="njit", type="office")
    conn.commit()
    return conn


def test_ingest_reuses_ist_org_writes_policy_and_people(tmp_path):
    conn = _conn(tmp_path)
    res = EntryResult(
        seed="https://ist.njit.edu/",
        staff=[StaffRecord("Blake Haggerty", "Interim Vice President", "Office of the VP")],
        prose=[ProsePage("Software Availability", "IST provides software.",
                         "https://ist.njit.edu/software-available-download")],
        skipped=[])
    out = ist_crawl.ingest_ist(conn, res)
    conn.commit()
    # Org reused, not duplicated
    n_ist = conn.execute("SELECT COUNT(*) FROM organizations WHERE slug='ist'").fetchone()[0]
    assert n_ist == 1
    # Prose is type='policy', created_by='crawler', active
    row = conn.execute("SELECT type, created_by, is_active FROM knowledge_items "
                       "WHERE org_id=? AND title='Software Availability'", (out["org_id"],)).fetchone()
    assert tuple(row) == ("policy", "crawler", 1)
    # Person created with a has_role edge under ist
    p = conn.execute("SELECT COUNT(*) FROM nodes WHERE type='Person' AND name='Blake Haggerty'").fetchone()[0]
    assert p == 1
    # No fabricated email/phone on the person
    import json
    attrs = conn.execute("SELECT attrs FROM nodes WHERE name='Blake Haggerty'").fetchone()[0]
    a = json.loads(attrs) if attrs else {}
    assert not a.get("email") and not a.get("phone")
    # F4/B5: the EOS contact-merge path is gone — there is no code that could write contact.
    assert not hasattr(ist_crawl, "_merge_person_attrs")


def test_ingest_idempotent_recrawl(tmp_path):
    conn = _conn(tmp_path)
    res = EntryResult("https://ist.njit.edu/", [],
        [ProsePage("Service Desk", "Hours: 8-5.", "https://ist.njit.edu/ist-service-desk")], [])
    a = ist_crawl.ingest_ist(conn, res); conn.commit()
    b = ist_crawl.ingest_ist(conn, res); conn.commit()
    assert a["prose_inserted"] == 1 and b["prose_unchanged"] == 1
