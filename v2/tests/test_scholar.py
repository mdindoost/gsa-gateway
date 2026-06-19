from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.ingestion.people_editor import set_person_profiles
from v2.core.ingestion.scholar import (
    parse_scholar_metrics, people_with_scholar, refresh_scholar,
)

# A trimmed copy of the real Scholar stats table (rows: Citations / h-index / i10-index;
# columns: All | Since 2020).
SCHOLAR_HTML = """
<html><body>
<table id="gsc_rsb_st"><tbody>
  <tr><th></th><th class="gsc_rsb_sth">All</th><th class="gsc_rsb_sth">Since 2020</th></tr>
  <tr><td class="gsc_rsb_sc1">Citations</td><td class="gsc_rsb_std">2,774</td><td class="gsc_rsb_std">1,402</td></tr>
  <tr><td class="gsc_rsb_sc1">h-index</td><td class="gsc_rsb_std">26</td><td class="gsc_rsb_std">19</td></tr>
  <tr><td class="gsc_rsb_sc1">i10-index</td><td class="gsc_rsb_std">35</td><td class="gsc_rsb_std">28</td></tr>
</tbody></table>
</body></html>
"""

BLOCKED_HTML = "<html><body>Please show you're not a robot</body></html>"


def test_parse_scholar_metrics_all_column():
    assert parse_scholar_metrics(SCHOLAR_HTML) == {
        "citations": 2774, "h_index": 26, "i10_index": 35}


def test_parse_scholar_metrics_none_on_blocked():
    assert parse_scholar_metrics(BLOCKED_HTML) is None
    assert parse_scholar_metrics("") is None


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'CS','cs','department')")
    c.execute("INSERT INTO nodes(type,key,name,attrs,source) VALUES('Person','p/k','Koutis',?,'crawler')",
              (json.dumps({"profiles": {"scholar": {"url": "https://scholar.google.com/k"}}}),))
    c.execute("INSERT INTO nodes(type,key,name,attrs,source) VALUES('Person','p/n','NoScholar','{}','crawler')")
    c.commit()
    yield c
    c.close()


def test_people_with_scholar_finds_only_those_with_url(conn):
    assert people_with_scholar(conn) == [("p/k", "https://scholar.google.com/k")]


def test_refresh_scholar_updates_metrics_keeping_url(conn):
    out = refresh_scholar(conn, fetch=lambda u: (SCHOLAR_HTML, "ok"), delay=0, today="2026-06")
    conn.commit()
    assert out == {"people": 1, "updated": 1, "failed": 0, "errors": []}
    sch = json.loads(conn.execute("SELECT attrs FROM nodes WHERE key='p/k'").fetchone()[0]
                     )["profiles"]["scholar"]
    assert sch["citations"] == 2774 and sch["h_index"] == 26 and sch["i10_index"] == 35
    assert sch["updated_at"] == "2026-06"
    assert sch["url"] == "https://scholar.google.com/k"     # url preserved


def test_refresh_scholar_counts_blocked_as_failed_without_touching_data(conn):
    out = refresh_scholar(conn, fetch=lambda u: (BLOCKED_HTML, "ok"), delay=0)
    assert out["updated"] == 0 and out["failed"] == 1
    sch = json.loads(conn.execute("SELECT attrs FROM nodes WHERE key='p/k'").fetchone()[0]
                     )["profiles"]["scholar"]
    assert "citations" not in sch                            # unchanged on failure
