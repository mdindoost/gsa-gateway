"""sweep() — long-running slow-drip orchestration over scholar_discovery.

All I/O injected (web_search/fetch/sleep, should_stop). Asserts the B1 termination fix,
the Brave budget ceiling, block-backoff give-up, SIGTERM interrupt, and jitter pacing —
with no network and no real waits.
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
from v2.tests.test_scholar_discovery_run import _profile, BLOCKED


@pytest.fixture()
def db():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    ywcc = ensure_org(c, "ywcc", "YWCC", parent_slug="njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", parent_slug="ywcc", type="department")
    sync_org_nodes(c)
    for k, n in [("p/a", "Aa Uniquesurnamea"), ("p/b", "Bb Uniquesurnameb"), ("p/c", "Cc Uniquesurnamec")]:
        project_appointment(c, person_key=k, name=n, org_id=cs, category="faculty",
                            titles=["Professor"], source_section="manual", source="dashboard")
    c.commit(); yield c; c.close()


def test_sweep_terminates_and_marks_skips(db):
    web = lambda q, **k: []                        # everyone is a skip
    s = D.sweep(db, web_search=web, fetch=lambda u: ("", "ok"), sleep=lambda x: None,
                org_scope="ywcc", chunk=2, brave_budget=100)
    assert s["stopped_reason"] == "done"
    assert s["scanned"] == 3                        # each searched exactly once across chunks
    # re-run: all attempted -> nothing to do (true resume / termination)
    s2 = D.sweep(db, web_search=web, fetch=lambda u: ("", "ok"), sleep=lambda x: None,
                 org_scope="ywcc", chunk=2, brave_budget=100)
    assert s2["scanned"] == 0 and s2["stopped_reason"] == "done"


def test_sweep_budget_ceiling_stops(db):
    s = D.sweep(db, web_search=lambda q, **k: [], fetch=lambda u: ("", "ok"), sleep=lambda x: None,
                org_scope="ywcc", chunk=10, brave_budget=2)
    assert s["brave_calls"] == 2 and s["stopped_reason"] == "budget"


def test_sweep_interrupt_finishes_current_then_stops(db):
    state = {"n": 0, "stop": False}
    def on_progress(stats, key, name, d):
        state["n"] += 1
        if state["n"] >= 2:                          # after 2 people, request stop
            state["stop"] = True
    s = D.sweep(db, web_search=lambda q, **k: [], fetch=lambda u: ("", "ok"), sleep=lambda x: None,
                org_scope="ywcc", chunk=10, brave_budget=100,
                should_stop=lambda: state["stop"], on_progress=on_progress)
    assert s["scanned"] == 2 and s["stopped_reason"] == "interrupted"


def test_sweep_gives_up_after_blocked_chunks(db):
    web = lambda q, **k: ["https://scholar.google.com/citations?user=BLK"]
    slept = []
    s = D.sweep(db, web_search=web, fetch=lambda u: (BLOCKED, "ok"), sleep=lambda x: slept.append(x),
                org_scope="ywcc", chunk=2, brave_budget=100,
                block_chunk_limit=1, max_blocked_chunks=2, backoff_seconds=999)
    assert s["stopped_reason"] == "blocked"
    assert 999 in slept                             # backoff slept between blocked chunks
    # blocked people are NOT marked (so a later run retries them)
    raw = db.execute("SELECT attrs FROM nodes WHERE key='p/a'").fetchone()[0]
    assert "discovery_attempted" not in ((json.loads(raw).get("profiles") or {}).get("scholar") or {})


def test_sweep_writes_strict_and_jitter_sleeps(db):
    web = lambda q, **k: ["https://scholar.google.com/citations?user=AAA"]
    fetch = lambda u: (_profile("Aa Uniquesurnamea"), "ok")
    slept = []
    s = D.sweep(db, web_search=web, fetch=fetch, sleep=lambda x: slept.append(x),
                org_scope="ywcc", chunk=10, brave_budget=100, jitter=(45, 100))
    assert s["written"] >= 1
    bag = json.loads(db.execute("SELECT attrs FROM nodes WHERE key='p/a'").fetchone()[0])["profiles"]["scholar"]
    assert bag["url"].startswith("https://scholar.google.com") and bag["discovered_by"] == "auto"
    assert any(45 <= x <= 100 for x in slept)
