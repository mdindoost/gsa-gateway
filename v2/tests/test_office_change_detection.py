import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion.office_ingest import _slug_from_url, content_hash, ingest_office_page

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


# ── I1: EOS multi-hub slug-collision tests (Gate0 I1) ─────────────────────────

def test_slug_from_url_no_collision_across_eos_hubs():
    """Two pages with the same tail segment under different EOS hubs must produce distinct slugs."""
    slug_parking = _slug_from_url("https://www.njit.edu/parking/contact")
    slug_mailroom = _slug_from_url("https://www.njit.edu/mailroom/contact")
    assert slug_parking != slug_mailroom, (
        f"slug collision: both /parking/contact and /mailroom/contact map to {slug_parking!r}"
    )


def test_slug_from_url_includes_full_path():
    """Full-path slugging: /parking/visitor becomes office/parking-visitor."""
    slug = _slug_from_url("https://www.njit.edu/parking/visitor")
    assert "parking" in slug, f"expected 'parking' in slug, got {slug!r}"
    assert "visitor" in slug, f"expected 'visitor' in slug, got {slug!r}"


def test_slug_from_url_index_fallback():
    """Root URL with no path tail produces a deterministic slug (not empty)."""
    slug = _slug_from_url("https://www.njit.edu/parking/")
    assert slug.startswith("office/") and len(slug) > len("office/")


def test_two_eos_hub_pages_produce_two_active_docs(tmp_path):
    """Ingesting two pages from different EOS hubs under the same org must yield TWO distinct
    active office_page docs — not one overwriting the other (the original collision bug)."""
    conn = create_all(str(tmp_path / "t.db"))
    oid = _org(conn)
    parking_text = "Visitor parking is available in the Lock Street Deck. " * 8
    mailroom_text = "The mailroom accepts packages Monday through Friday. " * 8
    with conn:
        n1, leg1 = ingest_office_page(
            conn, org_id=oid,
            url="https://www.njit.edu/parking/contact",
            title="Parking Contact",
            text=parking_text,
        )
        n2, leg2 = ingest_office_page(
            conn, org_id=oid,
            url="https://www.njit.edu/mailroom/contact",
            title="Mailroom Contact",
            text=mailroom_text,
        )
    assert leg1 == "chunk" and n1 >= 1
    assert leg2 == "chunk" and n2 >= 1
    active_docs = conn.execute(
        "SELECT DISTINCT json_extract(metadata,'$.doc_id') doc_id "
        "FROM knowledge_items WHERE type='office_page' AND is_active=1"
    ).fetchall()
    doc_ids = {r[0] for r in active_docs}
    assert len(doc_ids) == 2, (
        f"expected 2 distinct active office_page doc_ids, got {len(doc_ids)}: {doc_ids}"
    )
