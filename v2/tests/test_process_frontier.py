from __future__ import annotations
import json, sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.graph.project import project_appointment
from v2.core.ingestion.explore import process_frontier


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(4,1,'YWCC','ywcc','college')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(5,4,'Computer Science','computer-science','department')")
    pid = project_appointment(c, person_key="p/k", name="K", org_id=5, category="faculty",
                              titles=[], source_section="Professors")
    c.execute("INSERT INTO knowledge_items(org_id,type,content,metadata,created_by) "
              "VALUES(5,'profile','x',?, 'crawler')", (json.dumps({"entity_id": "p/k"}),))
    c.execute("INSERT INTO frontier(from_node_id,url,aspect,status) "
              "VALUES(?, 'https://k.example.com', 'people','pending')", (pid,))
    c.commit()
    yield c
    c.close()


def _fetch(pages):
    return lambda u: pages.get(u, (u, "", "error"))


def test_frontier_site_becomes_webpage_item_and_marked_fetched(conn):
    pages = {"https://k.example.com": ("https://k.example.com",
             "<html><body>My research on graphs and my students.</body></html>", "ok")}
    st = process_frontier(conn, _fetch(pages))
    assert st.fetched == 1
    row = conn.execute("SELECT org_id, content FROM knowledge_items WHERE is_active=1 "
                       "AND type='webpage' AND json_extract(metadata,'$.entity_id')='p/k'").fetchone()
    assert row and row[0] == 5 and "graphs" in row[1]           # filed under home dept, text captured
    assert conn.execute("SELECT status FROM frontier WHERE url='https://k.example.com'").fetchone()[0] == "fetched"


def test_frontier_idempotent_no_duplicate_webpage(conn):
    pages = {"https://k.example.com": ("https://k.example.com",
             "<html><body>same content</body></html>", "ok")}
    process_frontier(conn, _fetch(pages))
    conn.execute("UPDATE frontier SET status='pending'")            # force re-process
    process_frontier(conn, _fetch(pages))
    n = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND type='webpage' "
                     "AND json_extract(metadata,'$.entity_id')='p/k'").fetchone()[0]
    assert n == 1                                                   # deduped (unchanged)


def test_frontier_bad_fetch_marks_error(conn):
    process_frontier(conn, _fetch({}))                             # nothing serves the url -> error
    assert conn.execute("SELECT status FROM frontier WHERE url='https://k.example.com'").fetchone()[0] == "error"
