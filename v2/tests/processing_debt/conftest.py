import json, sqlite3
import pytest

@pytest.fixture
def fixture_db(tmp_path):
    db = tmp_path / "fixture.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
      CREATE TABLE nodes (id INTEGER PRIMARY KEY, key TEXT, type TEXT, name TEXT, attrs TEXT);
      CREATE TABLE edges (id INTEGER PRIMARY KEY, src_id INT, type TEXT, dst_id INT,
                          category TEXT, attrs TEXT, is_active INT DEFAULT 1);
      CREATE TABLE knowledge_items (id INTEGER PRIMARY KEY, org_id INT, type TEXT, title TEXT,
                          content TEXT, metadata TEXT, is_active INT DEFAULT 1);
      CREATE VIRTUAL TABLE knowledge_fts USING fts5(title, content, content='knowledge_items', content_rowid='id');
    """)
    # kg_probe target: a Person node with a role title in attrs/edges
    conn.execute("INSERT INTO nodes(id,key,type,name,attrs) VALUES (1,'people/pan-xu','Person','Pan Xu',?)",
                 (json.dumps({"office": "4310 Guttenberg Information Technologies Center (GITC)"}),))
    conn.execute("INSERT INTO nodes(id,key,type,name,attrs) VALUES (2,'org/cs','Org','Computer Science','{}')")
    conn.execute("INSERT INTO edges(id,src_id,type,dst_id,category,attrs) VALUES "
                 "(1,1,'has_role',2,'faculty',?)", (json.dumps({"titles": ["Assistant Professor"]}),))
    # fts_probe / grep_probe target in a NORMAL type
    conn.execute("INSERT INTO knowledge_items(id,type,title,content) VALUES "
                 "(10,'policy','MS CS Admission','MS in Computer Science requires a four-year computing degree.')")
    # excluded-type (publication) target — owned but normally excluded from answers → CONFIG stage
    conn.execute("INSERT INTO knowledge_items(id,type,title,content) VALUES "
                 "(11,'publication','Veil paper','Veil: A Storage and Communication Efficient Volume-Hiding Algorithm.')")
    conn.execute("INSERT INTO knowledge_fts(rowid,title,content) SELECT id,title,content FROM knowledge_items")
    conn.commit()
    conn.close()
    return str(db)
