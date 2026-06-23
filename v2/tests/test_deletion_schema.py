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
