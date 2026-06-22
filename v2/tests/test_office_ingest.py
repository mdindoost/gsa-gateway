import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion.office_ingest import ingest_office_page, is_high_stakes


def test_high_stakes_classifier():
    assert is_high_stakes("https://www.njit.edu/global/opt-cpt", "Apply for OPT ...")
    assert is_high_stakes("https://www.njit.edu/registrar/schedule", "Your balance is $750 due by Nov 15.")
    assert not is_high_stakes("https://www.njit.edu/parking/visitor-parking",
                              "Visitor parking is in the Lock Street Deck.")


def test_generic_page_goes_live_as_office_page(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        oid = ensure_org(conn, slug="eos", name="EOS", parent_slug=None, type="office")
        n, leg = ingest_office_page(conn, org_id=oid,
                                    url="https://www.njit.edu/parking/visitor-parking",
                                    title="Visitor Parking",
                                    text="Visitor parking is available in the Lock Street Deck. " * 8)
    assert leg == "chunk" and n >= 1
    row = conn.execute("SELECT type,is_active,created_by FROM knowledge_items LIMIT 1").fetchone()
    assert row["type"] == "office_page" and row["is_active"] == 1 and row["created_by"] == "crawler"


def test_high_stakes_page_is_staged_not_live(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        oid = ensure_org(conn, slug="global", name="OGI", parent_slug=None, type="office")
        n, leg = ingest_office_page(conn, org_id=oid,
                                    url="https://www.njit.edu/global/opt-cpt",
                                    title="OPT and CPT",
                                    text="OPT application steps: file Form I-765 within the deadline. " * 8)
    assert leg == "staged"
    row = conn.execute("SELECT is_active,json_extract(metadata,'$.stakes') s "
                       "FROM knowledge_items LIMIT 1").fetchone()
    assert row["is_active"] == 0 and row["s"] == "high"
