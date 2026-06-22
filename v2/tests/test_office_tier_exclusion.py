import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.retrieval.retriever import DEFAULT_EXCLUDE_TYPES, V2Retriever


def _seed(conn):
    with conn:
        oid = ensure_org(conn, slug="njit", name="NJIT", parent_slug=None, type="university")
        cur = conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,is_active,created_by) "
            "VALUES(?,?,?,?,1,'dashboard')", (oid, "policy", "Curated", "curated body"))
        curated_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,is_active,created_by) "
            "VALUES(?,?,?,?,1,'crawler')", (oid, "office_page", "Office", "office body"))
        office_id = cur.lastrowid
    return curated_id, office_id


def test_office_page_in_default_exclude_types():
    assert "office_page" in DEFAULT_EXCLUDE_TYPES


def test_default_retrieve_excludes_office_page_but_whitelist_includes_it(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    curated_id, office_id = _seed(conn)
    r = V2Retriever(conn, embedder=None)            # light ctor; _allowed_ids is pure SQL
    default_allowed = r._allowed_ids(None, None, None, exclude_types=r.exclude_types)
    assert office_id not in default_allowed and curated_id in default_allowed
    office_only = r._allowed_ids(None, None, ["office_page"], None)
    assert office_only == {office_id}
