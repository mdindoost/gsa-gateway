import sqlite3, tempfile, os, json
from autoeval.sampler import extract_person

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
