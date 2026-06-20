"""discover_for_person + run — orchestration with INJECTED search/fetch (no network).

Auto-writes only strict matches (with provenance tags), queues uncertain, aborts on consecutive
Scholar blocks, and respects a hard Brave-call cap.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
from v2.core.ingestion import scholar_discovery as D

_STATS = ("""<table id="gsc_rsb_st"><tbody>
<tr><th></th><th class="gsc_rsb_sth">All</th><th class="gsc_rsb_sth">Since 2020</th></tr>
<tr><td class="gsc_rsb_sc1">Citations</td><td class="gsc_rsb_std">2,774</td><td class="gsc_rsb_std">1,402</td></tr>
<tr><td class="gsc_rsb_sc1">h-index</td><td class="gsc_rsb_std">26</td><td class="gsc_rsb_std">19</td></tr>
<tr><td class="gsc_rsb_sc1">i10-index</td><td class="gsc_rsb_std">35</td><td class="gsc_rsb_std">28</td></tr>
</tbody></table>""")

def _profile(name, domain="njit.edu", aff="Computer Science, NJIT", interests=("Graph algorithms",)):
    ints = "".join(f'<a class="gsc_prf_inta">{i}</a>' for i in interests)
    return (f'<div id="gsc_prf_in">{name}</div><div id="gsc_prf_ila">{aff}</div>'
            f'<div id="gsc_prf_ivh">Verified email at {domain}</div>'
            f'<div id="gsc_prf_int">{ints}</div>{_STATS}')

BLOCKED = "<html><body>not a robot</body></html>"


@pytest.fixture()
def db():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    ywcc = ensure_org(c, "ywcc", "YWCC", parent_slug="njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", parent_slug="ywcc", type="department")
    sync_org_nodes(c)
    def appoint(key, name):
        project_appointment(c, person_key=key, name=name, org_id=cs, category="faculty",
                            titles=["Professor"], source_section="manual", source="dashboard")
    appoint("p/koutis", "Ioannis Koutis")
    c.commit()
    yield c
    c.close()


def test_strict_match_is_written_with_provenance(db):
    web = lambda q, **k: ["https://scholar.google.com/citations?user=AAA&hl=en"]
    fetch = lambda u: (_profile("Ioannis Koutis"), "ok")
    stats = D.run(db, web_search=web, fetch=fetch, org_scope="ywcc", delay=0)
    assert stats["written"] == 1 and stats["queued"] == 0
    bag = json.loads(db.execute("SELECT attrs FROM nodes WHERE key='p/koutis'").fetchone()[0])["profiles"]["scholar"]
    assert bag["url"].startswith("https://scholar.google.com")
    assert bag["citations"] == 2774
    assert bag["discovered_by"] == "auto" and bag["match_basis"] == "unique_surname"
    assert bag["discovered_at"]


def test_no_scholar_result_is_skipped_not_written(db):
    stats = D.run(db, web_search=lambda q, **k: [], fetch=lambda u: ("", "ok"), org_scope="ywcc", delay=0)
    assert stats["written"] == 0 and stats["skipped"] == 1


def test_non_njit_candidate_not_written(db):
    web = lambda q, **k: ["https://scholar.google.com/citations?user=ZZZ"]
    fetch = lambda u: (_profile("Ioannis Koutis", domain="unh.edu"), "ok")
    stats = D.run(db, web_search=web, fetch=fetch, org_scope="ywcc", delay=0)
    assert stats["written"] == 0


def test_run_aborts_after_consecutive_blocks(db):
    # add more faculty so there are several targets
    for k, n in [("p/a", "Alpha Oneunique"), ("p/b", "Beta Twounique"), ("p/c", "Gamma Threeunique")]:
        project_appointment(db, person_key=k, name=n, org_id=2, category="faculty",
                            titles=["Professor"], source_section="manual", source="dashboard")
    db.commit()
    web = lambda q, **k: ["https://scholar.google.com/citations?user=BLK"]
    fetch = lambda u: (BLOCKED, "ok")
    stats = D.run(db, web_search=web, fetch=fetch, org_scope="ywcc", delay=0, block_abort=2)
    assert stats["blocked"] >= 2
    assert stats["written"] == 0


def test_brave_hard_cap_limits_calls(db):
    for k, n in [("p/a", "Alpha Onenum"), ("p/b", "Beta Twonum")]:
        project_appointment(db, person_key=k, name=n, org_id=2, category="faculty",
                            titles=["Professor"], source_section="manual", source="dashboard")
    db.commit()
    calls = {"n": 0}
    def web(q, **k):
        calls["n"] += 1
        return []
    D.run(db, web_search=web, fetch=lambda u: ("", "ok"), org_scope="ywcc", delay=0, max_brave=1)
    assert calls["n"] == 1


def test_run_marks_skip_attempted_and_excludes_on_rerun(db):
    web = lambda q, **k: []                      # no candidates -> skip
    fetch = lambda u: ("", "ok")
    s1 = D.run(db, web_search=web, fetch=fetch, org_scope="ywcc", delay=0)
    assert s1["skipped"] >= 1
    sch = json.loads(db.execute("SELECT attrs FROM nodes WHERE key='p/koutis'").fetchone()[0]
                     )["profiles"]["scholar"]
    assert sch["discovery_attempted"]["decision"] == "skip"     # marked
    s2 = D.run(db, web_search=web, fetch=fetch, org_scope="ywcc", delay=0)
    assert s2["scanned"] == 0                                   # excluded on re-run -> terminates


def test_run_does_not_mark_blocked(db):
    web = lambda q, **k: ["https://scholar.google.com/citations?user=BLK"]
    fetch = lambda u: (BLOCKED, "ok")
    D.run(db, web_search=web, fetch=fetch, org_scope="ywcc", delay=0, block_abort=5)
    raw = db.execute("SELECT attrs FROM nodes WHERE key='p/koutis'").fetchone()[0]
    sch = (json.loads(raw).get("profiles") or {}).get("scholar") or {}
    assert "discovery_attempted" not in sch                     # blocked = transient, retry later
