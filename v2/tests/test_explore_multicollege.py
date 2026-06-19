"""Multi-college KG expansion (2026-06-18): policy-routed listings through explore().

Covers the two new behaviors the design hinges on:
- college_admin_only: a college /our-people page is a faculty ROLL-UP; only its admin/staff
  sections appoint to the college, the faculty sections are skipped (the department listing owns
  them). A faculty member on BOTH the college page and their dept page → one node, dept edge only.
- hcad_split: one listing routed to two school orgs by section; university-library cross-list skipped.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.ingestion.entry_points import EntryPoint
from v2.core.ingestion.explore import explore


def make_fetch(pages):
    def fetch(url):
        return (url, pages[url], "ok") if url in pages else (url, "", "error")
    return fetch


def _roles(conn, key):
    pid = conn.execute("SELECT id FROM nodes WHERE type='Person' AND key=?", (key,)).fetchone()
    if not pid:
        return {}
    return dict(conn.execute(
        "SELECT o.key, e.category FROM edges e JOIN nodes o ON o.id=e.dst_id "
        "WHERE e.src_id=? AND e.type='has_role' AND e.is_active=1", (pid[0],)).fetchall())


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    # seed the org tree (mirrors entry_points.SEED_ORGS)
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(parent_id,name,slug,type) VALUES(1,'NCE','nce','college')")
    c.execute("INSERT INTO organizations(parent_id,name,slug,type) VALUES(1,'HCAD','hcad','college')")
    hcad = c.execute("SELECT id FROM organizations WHERE slug='hcad'").fetchone()[0]
    c.execute("INSERT INTO organizations(parent_id,name,slug,type) VALUES(?,'NJSoA','njsoa','school')", (hcad,))
    c.execute("INSERT INTO organizations(parent_id,name,slug,type) VALUES(?,'Art+Design','art-design','school')", (hcad,))
    c.commit()
    yield c
    c.close()


# ── college_admin_only ─────────────────────────────────────────────────────────────
NCE_COLLEGE_HTML = """
<h4>Office of the Dean Administration</h4>
<a href="//people.njit.edu/profile/kam" class="column"><h1 class="name">Kam, Moshe</h1>
  <p class="title">Dean</p></a>
<h4>Professors</h4>
<a href="//people.njit.edu/profile/bandelt" class="column"><h1 class="name">Bandelt, Matthew</h1>
  <p class="title">Associate Professor</p></a>
"""
CIVIL_HTML = """
<h4>Professors</h4>
<a href="//people.njit.edu/profile/bandelt" class="column"><h1 class="name">Bandelt, Matthew</h1>
  <p class="title">Associate Professor</p></a>
"""

def test_college_admin_only_skips_rollup_faculty_keeps_admin(conn):
    college = EntryPoint("https://engineering.njit.edu/our-people", "nce",
                         "NCE", "listing", parent_slug="njit", org_type="college",
                         policy="college_admin_only")
    civil = EntryPoint("https://civil.njit.edu/people", "civil-environmental-engineering",
                       "Civil & Environmental Engineering", "listing",
                       parent_slug="nce", org_type="department")
    pages = {college.url: NCE_COLLEGE_HTML, civil.url: CIVIL_HTML}
    explore(conn, make_fetch(pages), start=college, depth=2)
    explore(conn, make_fetch(pages), start=civil, depth=2)

    # Dean → college admin edge; Bandelt (faculty) → NO college edge, only his department
    assert _roles(conn, "people.njit.edu/profile/kam") == {"nce": "admin"}
    assert _roles(conn, "people.njit.edu/profile/bandelt") == \
        {"civil-environmental-engineering": "faculty"}
    # exactly one Person node for the cross-listed faculty member
    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE type='Person' AND "
                        "key='people.njit.edu/profile/bandelt'").fetchone()[0] == 1


# ── hcad_split ─────────────────────────────────────────────────────────────────────
HCAD_HTML = """
<h4>Leadership</h4>
<a href="//people.njit.edu/profile/dean" class="column"><h1 class="name">Sevtsuk, Andres</h1>
  <p class="title">Dean</p></a>
<h4>Architecture Faculty</h4>
<a href="//people.njit.edu/profile/arch1" class="column"><h1 class="name">Arch, Anna</h1>
  <p class="title">Professor</p></a>
<h4>Art + Design Faculty</h4>
<a href="//people.njit.edu/profile/art1" class="column"><h1 class="name">Art, Andy</h1>
  <p class="title">Professor</p></a>
<h4>Library Staff</h4>
<a href="//people.njit.edu/profile/lib1" class="column"><h1 class="name">Lib, Lee</h1>
  <p class="title">Librarian</p></a>
"""

def test_hcad_split_routes_schools_and_skips_library(conn):
    hcad = EntryPoint("https://design.njit.edu/our-people", "hcad",
                      "HCAD", "listing", parent_slug="njit", org_type="college",
                      policy="hcad_split")
    explore(conn, make_fetch({hcad.url: HCAD_HTML}), start=hcad, depth=2)

    assert _roles(conn, "people.njit.edu/profile/arch1") == {"njsoa": "faculty"}
    assert _roles(conn, "people.njit.edu/profile/art1") == {"art-design": "faculty"}
    assert _roles(conn, "people.njit.edu/profile/dean") == {"hcad": "admin"}
    # university library staff cross-listed on the HCAD page → no HCAD appointment
    assert _roles(conn, "people.njit.edu/profile/lib1") == {}
