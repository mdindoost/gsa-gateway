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


def test_explore_rerun_skips_unchanged(conn):
    explore(conn, make_fetch(PAGES), depth=2)
    st2 = explore(conn, make_fetch(PAGES), depth=2)
    assert st2.skipped_unchanged >= 3 and st2.appointments == 0
