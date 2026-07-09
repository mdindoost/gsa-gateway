import sqlite3
import pytest
from v2.core.database.schema import get_connection
from v2.core.retrieval import router
from v2.core.retrieval.router import route

def _mk(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE organizations (id INTEGER PRIMARY KEY, type TEXT, parent_id INTEGER)")
    conn.executemany("INSERT INTO organizations (id,type,parent_id) VALUES (?,?,?)",
        [(1,"university",None),(2,"college",1),(3,"department",2),(4,"club",1),(5,"gsa",1)])
    return conn

def test_leader_intent_matches_slang():
    assert router._LEADER_INTENT.search("who run cs")
    assert router._LEADER_INTENT.search("boss of ywcc")
    assert router._LEADER_INTENT.search("who president cs")
    assert not router._LEADER_INTENT.search("who is the chair of cs")   # role-vocab path owns this

def test_leader_role_by_org_type(tmp_path):
    conn = _mk(tmp_path)
    assert router._leader_role_for_org(conn, 3) == ("people_by_role", "chair")   # department
    assert router._leader_role_for_org(conn, 2) == ("people_by_role", "dean")    # college
    assert router._leader_role_for_org(conn, 1) == ("people_by_role", "president")# university
    assert router._leader_role_for_org(conn, 4) == ("officers_in_org", "")       # club
    assert router._leader_role_for_org(conn, 5) == ("officers_in_org", "")       # gsa


# ── integration (live DB), wiring into route() ─────────────────────────────────
@pytest.fixture
def conn():
    c = get_connection("gsa_gateway.db")
    yield c
    c.close()


def test_who_run_cs_routes_chair(conn, monkeypatch):
    monkeypatch.setenv("QUERY_CORRECT_ENABLED", "1")
    r = route(conn, "who run cs computer science")   # dictionary already expanded cs
    assert r is not None and r.skill == "people_by_role" and r.args.get("role_head") == "chair"


def test_boss_of_ywcc_routes_dean(conn, monkeypatch):
    monkeypatch.setenv("QUERY_CORRECT_ENABLED", "1")
    r = route(conn, "boss of ywcc")
    assert r is not None and r.skill == "people_by_role" and r.args.get("role_head") == "dean"


def test_leader_rule_off_by_flag(conn, monkeypatch):
    monkeypatch.delenv("QUERY_CORRECT_ENABLED", raising=False)
    assert route(conn, "who run cs computer science") is None   # unchanged when off


def test_registrar_office_hours_still_office(conn, monkeypatch):
    monkeypatch.setenv("QUERY_CORRECT_ENABLED", "1")
    r = route(conn, "registrar office hours")
    assert r is None or r.skill != "people_by_role"   # role_is_org guard held
