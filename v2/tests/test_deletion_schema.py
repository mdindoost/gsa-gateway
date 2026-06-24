import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_deletion_columns_exist():
    conn = create_all(":memory:")
    assert {"delete_at", "deleted_at"} <= _cols(conn, "posts")
    assert {"delete_status", "deleted_at", "delete_error", "delete_attempts"} <= _cols(
        conn, "post_deliveries")
    conn.close()


def test_create_all_seeds_auto_delete_default_on_root(tmp_path):
    db = str(tmp_path / "t.db")
    c = create_all(db)
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'N','njit','university')")
    c.commit(); c.close()
    c2 = create_all(db)                       # root now exists → seed fires (idempotent)
    v = c2.execute("SELECT value,type FROM settings WHERE org_id=1 AND key='default.auto_delete_hours'").fetchone()
    assert v is not None and v[0] == "24" and v[1] == "int"
    c2.close()
    c3 = create_all(db)                       # third run must NOT duplicate
    n = c3.execute("SELECT COUNT(*) FROM settings WHERE key='default.auto_delete_hours'").fetchone()[0]
    assert n == 1
    c3.close()


def test_delete_due_partial_index_exists():
    # the 30s poll selects WHERE delete_at<=now AND deleted_at IS NULL — a partial index keeps
    # that O(due-rows), not a full scan.
    conn = create_all(":memory:")
    names = {r[1] for r in conn.execute("PRAGMA index_list(posts)")}
    assert "idx_posts_delete_due" in names
    conn.close()
