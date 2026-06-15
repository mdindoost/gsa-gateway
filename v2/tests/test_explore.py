from __future__ import annotations
import sys
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.ingestion.explore import explore

HUB = """
<a href="https://computing.njit.edu/administration">College Administration Learn More</a>
<a href="https://cs.njit.edu/faculty">Computer Science Learn More</a>
"""
ADMIN = """
<h4>Associate Deans</h4>
<a href="//people.njit.edu/profile/gwang" class="column">
  <h1 class="name">Wang, Guiling</h1>
  <p class="title">Distinguished Professor</p><p class="title">Associate Dean of Research</p></a>
<h4>Staff</h4>
<a href="//people.njit.edu/profile/mg833" class="column">
  <h1 class="name">Giorgio, Michael</h1><p class="title">Director of Marketing</p></a>
"""
CS = """
<h4>Professors</h4>
<a href="//people.njit.edu/profile/gwang" class="column">
  <h1 class="name">Wang, Guiling</h1><p class="title">Distinguished Professor</p></a>
<a href="//people.njit.edu/profile/oria" class="column">
  <h1 class="name">Oria, Vincent</h1><p class="title">Professor</p></a>
"""

PAGES = {
    "https://computing.njit.edu/people": HUB,
    "https://computing.njit.edu/administration": ADMIN,
    "https://cs.njit.edu/faculty": CS,
}

def make_fetch(pages):
    def fetch(url):
        if url in pages:
            return url, pages[url], "ok"
        return url, "", "error"
    return fetch


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(4,1,'Ying Wu College of Computing','ywcc','college')")
    c.commit()
    yield c
    c.close()


def test_explore_depth2_builds_cross_path_appointments(conn):
    st = explore(conn, make_fetch(PAGES), depth=2)
    # Wang appears in College Admin (admin) AND CS (faculty) -> ONE node, TWO appointments
    pid = conn.execute("SELECT id FROM nodes WHERE type='Person' AND key=?",
                       ("people.njit.edu/profile/gwang",)).fetchone()[0]
    cats = sorted(r[0] for r in conn.execute(
        "SELECT category FROM edges WHERE src_id=? AND type='has_role' AND is_active=1", (pid,)))
    assert cats == ["admin", "faculty"]
    # College Administration org was created on demand
    assert conn.execute("SELECT 1 FROM organizations WHERE slug='college-administration'").fetchone()
    # profiles (not fetched at depth 2) are recorded as frontier next-steps
    assert conn.execute("SELECT COUNT(*) FROM frontier WHERE url LIKE '%/profile/%'").fetchone()[0] >= 2
    assert st.appointments >= 3   # Wang x2 + Giorgio + Oria (gwang counted in both listings)


def test_dean_appointment_lands_on_parent_college_not_admin_unit(conn):
    # Option A: a Dean / Associate Dean leads the COLLEGE, so their admin appointment
    # is filed on the parent org (YWCC), not the 'College Administration' sub-unit.
    # Wang is listed under 'Associate Deans' in the administration listing.
    explore(conn, make_fetch(PAGES), depth=2)
    pid = conn.execute("SELECT id FROM nodes WHERE type='Person' AND key=?",
                       ("people.njit.edu/profile/gwang",)).fetchone()[0]
    admin_dsts = {r[0] for r in conn.execute(
        "SELECT o.key FROM edges e JOIN nodes o ON o.id=e.dst_id "
        "WHERE e.src_id=? AND e.type='has_role' AND e.category='admin' AND e.is_active=1",
        (pid,)).fetchall()}
    assert admin_dsts == {"ywcc"}                       # dean role on the college, not the unit
    assert "college-administration" not in admin_dsts


def test_explore_rerun_skips_unchanged(conn):
    explore(conn, make_fetch(PAGES), depth=2)
    st2 = explore(conn, make_fetch(PAGES), depth=2)
    assert st2.skipped_unchanged >= 3 and st2.appointments == 0


def test_depth3_profile_enriches_without_clobbering_listing_role(conn):
    # Listings OWN appointments (section = authoritative role). A person listed under
    # College Administration "Staff" must STAY staff after their profile is fetched — the
    # profile pass only enriches (research/attrs), it never creates/clobbers a role (so an
    # '…Office of the Dean' title suffix can't flip a staff member to admin/faculty).
    fixture = (Path(__file__).parent / "fixtures" / "koutis_profile.html").read_text(encoding="utf-8")
    pages = {
        "https://computing.njit.edu/people":
            '<a href="https://computing.njit.edu/administration">College Administration Learn More</a>',
        "https://computing.njit.edu/administration":
            '<h4>Staff</h4><a href="//people.njit.edu/profile/ikoutis" class="column">'
            '<h1 class="name">Koutis, Ioannis</h1><p class="title">Staff Member</p></a>',
        "https://people.njit.edu/profile/ikoutis": fixture,
    }
    explore(conn, make_fetch(pages), depth=3)
    pid = conn.execute("SELECT id FROM nodes WHERE key='people.njit.edu/profile/ikoutis'").fetchone()[0]
    roles = dict(conn.execute(
        "SELECT o.key, e.category FROM edges e JOIN nodes o ON o.id=e.dst_id "
        "WHERE e.src_id=? AND e.type='has_role' AND e.is_active=1", (pid,)).fetchall())
    assert roles == {"college-administration": "staff"}     # only the listing role; not clobbered
    n_areas = conn.execute("SELECT COUNT(*) FROM edges WHERE src_id=? AND type='researches' "
                           "AND is_active=1", (pid,)).fetchone()[0]
    assert n_areas >= 1                                     # graph: profile enriched research
    # text layer: the profile is decomposed into knowledge_items (KB tab + RAG corpus)
    n_ki = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 "
                        "AND json_extract(metadata,'$.entity_id')=?",
                        ("people.njit.edu/profile/ikoutis",)).fetchone()[0]
    assert n_ki >= 1


def test_home_dept_org_id_prefers_department_over_admin_unit(conn):
    from v2.core.graph.project import project_appointment
    from v2.core.ingestion.explore import _home_dept_org_id
    conn.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
                 "VALUES(5,4,'Computer Science','computer-science','department')")
    conn.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
                 "VALUES(20,4,'College Administration','college-administration','unit')")
    conn.commit()
    pid = project_appointment(conn, person_key="p/w", name="W", org_id=20,
                              category="admin", titles=[], source_section="Associate Deans")
    project_appointment(conn, person_key="p/w", name="W", org_id=5,
                        category="faculty", titles=[], source_section="Professors")
    assert _home_dept_org_id(conn, pid) == 5            # CS dept, not the admin unit
    pid2 = project_appointment(conn, person_key="p/s", name="S", org_id=20,
                               category="staff", titles=[], source_section="Staff")
    assert _home_dept_org_id(conn, pid2) is None        # pure staff: no dept appointment


def test_explore_builds_full_org_hierarchy_in_kg(conn):
    explore(conn, make_fetch(PAGES), depth=2)
    keys = {r[0] for r in conn.execute("SELECT key FROM nodes WHERE type='Org' AND is_active=1")}
    assert {"njit", "ywcc", "computer-science"} <= keys     # roots are nodes, not just depts
    def onode(slug):
        return conn.execute("SELECT id FROM nodes WHERE type='Org' AND key=?", (slug,)).fetchone()[0]
    assert conn.execute("SELECT 1 FROM edges WHERE src_id=? AND type='part_of' AND dst_id=?",
                        (onode("ywcc"), onode("njit"))).fetchone()           # YWCC part_of NJIT
    assert conn.execute("SELECT 1 FROM edges WHERE src_id=? AND type='part_of' AND dst_id=?",
                        (onode("computer-science"), onode("ywcc"))).fetchone()  # CS part_of YWCC
