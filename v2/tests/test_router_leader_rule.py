import sqlite3
from v2.core.retrieval import router

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
