import sqlite3, tempfile, os, json
from autoeval.sampler import extract_person, extract_org, sample_items

def _person_db():
    fd, p = tempfile.mkstemp(suffix=".db"); os.close(fd)
    c = sqlite3.connect(p)
    c.executescript("""
      CREATE TABLE nodes(id INTEGER PRIMARY KEY, type TEXT, key TEXT, name TEXT,
                         attrs TEXT, is_active INT);
      CREATE TABLE edges(id INTEGER PRIMARY KEY, src_id INT, dst_id INT, type TEXT,
                         category TEXT, area_source TEXT, attrs TEXT, is_active INT);
      CREATE TABLE knowledge_items(id INTEGER PRIMARY KEY, type TEXT, content TEXT,
                         metadata TEXT, is_active INT);
    """)
    c.execute("INSERT INTO nodes VALUES(1,'Person','crawler/jane-doe','Doe, Jane',?,1)",
              (json.dumps({"email": "jdoe@njit.edu", "office": "GITC 4000"}),))
    c.commit(); c.close(); return p

def test_extract_person_fields_and_gaps():
    p = _person_db()
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
    item = extract_person(conn, "crawler/jane-doe")
    assert item.item_type == "person" and item.item_key == "crawler/jane-doe"
    assert item.ground_truth["email"] == "jdoe@njit.edu"
    assert "email" in item.has_fields and "office" in item.has_fields
    assert "phone" in item.missing_fields   # not on the node -> data-gap fuel

def _multi_person_db(n):
    fd, p = tempfile.mkstemp(suffix=".db"); os.close(fd)
    c = sqlite3.connect(p)
    c.executescript("""
      CREATE TABLE nodes(id INTEGER PRIMARY KEY, type TEXT, key TEXT, name TEXT,
                         attrs TEXT, is_active INT);
      CREATE TABLE edges(id INTEGER PRIMARY KEY, src_id INT, dst_id INT, type TEXT,
                         category TEXT, area_source TEXT, attrs TEXT, is_active INT);
      CREATE TABLE knowledge_items(id INTEGER PRIMARY KEY, type TEXT, content TEXT,
                         metadata TEXT, is_active INT);
      CREATE TABLE organizations(id INTEGER PRIMARY KEY, name TEXT, type TEXT,
                         metadata TEXT, is_active INT);
    """)
    for i in range(n):
        c.execute("INSERT INTO nodes VALUES(?,'Person',?,?,?,1)",
                  (i + 1, f"crawler/person-{i}", f"Doe, Jane {i}",
                   json.dumps({"email": f"jdoe{i}@njit.edu"})))
    c.commit(); c.close(); return p

def test_extract_org_excludes_kg_internal_structure():
    # ground_truth must carry ONLY user-facing facts (name/type/members) — never aliases or a
    # field list, which produced junk meta-questions ("how many aliases", "which fields").
    fd, p = tempfile.mkstemp(suffix=".db"); os.close(fd)
    c = sqlite3.connect(p)
    c.executescript("""
      CREATE TABLE nodes(id INTEGER PRIMARY KEY, type TEXT, key TEXT, name TEXT, attrs TEXT, is_active INT);
      CREATE TABLE edges(id INTEGER PRIMARY KEY, src_id INT, dst_id INT, type TEXT,
                         category TEXT, area_source TEXT, attrs TEXT, is_active INT);
      CREATE TABLE knowledge_items(id INTEGER PRIMARY KEY, type TEXT, content TEXT, metadata TEXT, is_active INT);
      CREATE TABLE organizations(id INTEGER PRIMARY KEY, name TEXT, type TEXT, metadata TEXT, is_active INT);
    """)
    c.execute("INSERT INTO organizations VALUES(59,'Study Abroad','office',?,1)",
              (json.dumps({"aliases": ["study-abroad", "SA"]}),))
    c.commit(); c.close()
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
    item = extract_org(conn, 59)
    assert item.ground_truth == {"name": "Study Abroad", "type": "office", "members": []}
    assert "aliases" not in item.ground_truth and "aliases" not in item.has_fields
    assert "aliases" not in item.missing_fields          # aliases never becomes a targetable gap
    assert item.missing_fields == ["members"]

def test_sample_items_returns_exactly_n_when_org_pool_empty():
    p = _multi_person_db(5)
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
    mix = {"person": 0.5, "org": 0.2, "area": 0.15, "chunk": 0.15}
    items = sample_items(conn, mix, 4, seed=1)
    assert len(items) == 4
