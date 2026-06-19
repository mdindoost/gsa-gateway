from __future__ import annotations
import json, sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.graph.project import project_appointment
from v2.core.graph.store import upsert_node
from v2.core.ingestion.explore import explore, reconcile_departures


def _card(slug, name):
    return (f'<a href="//people.njit.edu/profile/{slug}" class="column">'
            f'<h1 class="name">{name}</h1><p class="title">Professor</p></a>')


def _fetch(pages):
    return lambda u: (u, pages[u], "ok") if u in pages else (u, "", "error")


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(4,1,'YWCC','ywcc','college')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(5,4,'Computer Science','computer-science','department')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(6,4,'Data Science','data-science','department')")
    c.commit()
    yield c
    c.close()


def _appt_active(conn, slug):
    return conn.execute(
        "SELECT e.is_active FROM edges e JOIN nodes p ON p.id=e.src_id "
        "WHERE p.key=? AND e.type='has_role'", ("people.njit.edu/profile/" + slug,)).fetchone()


def test_section_scoped_sweep_retires_departed_appointment(conn):
    hub = '<a href="https://cs.njit.edu/faculty">Computer Science Learn More</a>'
    v1 = "<h4>Professors</h4>" + _card("a", "A") + _card("b", "B")
    v2 = "<h4>Professors</h4>" + _card("a", "A") + _card("c", "C")   # B left, C joined
    explore(conn, _fetch({"https://computing.njit.edu/people": hub,
                          "https://cs.njit.edu/faculty": v1}), depth=2)
    assert _appt_active(conn, "b")[0] == 1
    explore(conn, _fetch({"https://computing.njit.edu/people": hub,
                          "https://cs.njit.edu/faculty": v2}), depth=2)
    assert _appt_active(conn, "a")[0] == 1          # stayed
    assert _appt_active(conn, "b")[0] == 0          # departed -> retired
    assert _appt_active(conn, "c")[0] == 1          # joined


def test_reconcile_departures_fully_removes_person_with_no_appointment(conn):
    pid = upsert_node(conn, type="Person", key="p/gone", name="Gone", source="crawler")
    conn.execute("INSERT INTO knowledge_items(org_id,type,content,metadata,created_by) "
                 "VALUES(5,'profile','x',?,'crawler')", (json.dumps({"entity_id": "p/gone"}),))
    # an enrichment item from another source must also be dropped on full departure
    # (else it orphans, pointing at a deactivated node).
    conn.execute("INSERT INTO knowledge_items(org_id,type,content,metadata,created_by) "
                 "VALUES(5,'scholar_profile','y',?,'scholar')", (json.dumps({"entity_id": "p/gone"}),))
    conn.commit()
    out = reconcile_departures(conn)
    assert out["departed_people"] == 1
    assert conn.execute("SELECT is_active FROM nodes WHERE id=?", (pid,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND "
                        "json_extract(metadata,'$.entity_id')='p/gone'").fetchone()[0] == 0


def test_reconcile_departures_clears_stale_dept_kb_after_move(conn):
    project_appointment(conn, person_key="p/mv", name="MV", org_id=6, category="faculty",
                        titles=[], source_section="Professors")          # home now DS
    conn.execute("INSERT INTO knowledge_items(org_id,type,content,metadata,created_by) "
                 "VALUES(5,'profile','old',?,'crawler')", (json.dumps({"entity_id": "p/mv"}),))  # stale CS
    conn.execute("INSERT INTO knowledge_items(org_id,type,content,metadata,created_by) "
                 "VALUES(6,'profile','new',?,'crawler')", (json.dumps({"entity_id": "p/mv"}),))  # DS
    conn.commit()
    reconcile_departures(conn)
    orgs = {r[0] for r in conn.execute("SELECT org_id FROM knowledge_items WHERE is_active=1 AND "
            "json_extract(metadata,'$.entity_id')='p/mv'")}
    assert orgs == {6}                              # stale CS item retired, DS kept


def test_reconcile_refiles_college_filed_kb_to_home_department(conn):
    # A department chair reached via the college roll-up page: appointed to the college (ywcc, 4)
    # AND their department (CS, 5). Their profile was processed before the dept appointment
    # existed, so KB landed under the COLLEGE and no dept copy exists. reconcile must RE-FILE it
    # under the home department (not retire it, which would leave zero KB).
    project_appointment(conn, person_key="p/chair", name="Chair", org_id=4, category="admin",
                        titles=[], source_section="Department Chairs")   # college (ywcc)
    project_appointment(conn, person_key="p/chair", name="Chair", org_id=5, category="faculty",
                        titles=[], source_section="Professors")          # home department (CS)
    conn.execute("INSERT INTO knowledge_items(org_id,type,content,metadata,created_by) "
                 "VALUES(4,'profile','bio',?,'crawler')",
                 (json.dumps({"entity_id": "p/chair", "natural_key": "profile:p/chair"}),))
    conn.commit()
    out = reconcile_departures(conn)
    assert out["items_refiled"] == 1
    orgs = {r[0] for r in conn.execute("SELECT org_id FROM knowledge_items WHERE is_active=1 AND "
            "json_extract(metadata,'$.entity_id')='p/chair'")}
    assert orgs == {5}                              # re-filed from college (4) → home dept CS (5)


# ── multi-page-per-org: merge titles + M3 union (comprehensive crawl, task #8) ──────
from v2.core.ingestion.entry_points import EntryPoint
from v2.core.ingestion.explore import _new_m3_acc, _m3_sweep, ExploreStats


def _card2(slug, name, title):
    return (f'<a href="//people.njit.edu/profile/{slug}" class="column">'
            f'<h1 class="name">{name}</h1><p class="title">{title}</p></a>')


def test_merge_titles_across_pages_into_one_edge(conn):
    fac = EntryPoint("https://cs/faculty", "computer-science", "CS", "listing", parent_slug="ywcc")
    adm = EntryPoint("https://cs/admin", "computer-science", "CS", "listing", parent_slug="ywcc")
    pages = {"https://cs/faculty": "<h4>Professors</h4>" + _card2("oria", "Oria, Vincent", "Professor"),
             "https://cs/admin": "<h4>Administration</h4>" + _card2("oria", "Oria, Vincent", "Director")
                                  + _card2("staff", "Staff, Sam", "Administrative Assistant")}
    acc = _new_m3_acc()
    explore(conn, _fetch(pages), start=fac, depth=1, acc=acc)   # faculty first → canonical edge
    explore(conn, _fetch(pages), start=adm, depth=1, acc=acc)   # admin merges
    row = conn.execute("SELECT e.attrs, e.category FROM edges e JOIN nodes p ON p.id=e.src_id "
                       "JOIN nodes o ON o.id=e.dst_id WHERE p.key='people.njit.edu/profile/oria' "
                       "AND o.key='computer-science' AND e.type='has_role' AND e.is_active=1").fetchone()
    assert json.loads(row[0])["titles"] == ["Professor", "Director"]   # merged, faculty-first
    assert row[1] == "faculty"                                          # category preserved (not flipped)
    assert conn.execute("SELECT 1 FROM nodes WHERE key='people.njit.edu/profile/staff' "
                        "AND is_active=1").fetchone()                   # admin-only staff captured


def test_m3_union_does_not_cross_delete_admin_only_staff(conn):
    fac = EntryPoint("https://cs/faculty", "computer-science", "CS", "listing", parent_slug="ywcc")
    adm = EntryPoint("https://cs/admin", "computer-science", "CS", "listing", parent_slug="ywcc")
    pages = {"https://cs/faculty": "<h4>Professors</h4>" + _card2("a", "A, A", "Professor"),
             "https://cs/admin": "<h4>Administration</h4>" + _card2("b", "B, B", "Administrative Assistant")}
    acc = _new_m3_acc()
    explore(conn, _fetch(pages), start=fac, depth=1, acc=acc)
    explore(conn, _fetch(pages), start=adm, depth=1, acc=acc)
    _m3_sweep(conn, acc, ExploreStats())          # union sweep — neither page deletes the other's people
    assert _appt_active(conn, "a")[0] == 1        # faculty-only kept
    assert _appt_active(conn, "b")[0] == 1        # admin-only kept (the bug we're fixing)
