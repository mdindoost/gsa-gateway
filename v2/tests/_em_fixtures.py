"""Shared in-memory fixture helpers for entity_mentions tests."""
from v2.core.database.schema import create_knowledge_schema


def new_db():
    conn = create_knowledge_schema(":memory:")
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    conn.commit()
    return conn


def add_person(conn, key, name):
    conn.execute("INSERT INTO nodes(type,key,name,source,is_active) VALUES('Person',?,?,'test',1)",
                 (key, name))


def add_item(conn, typ, title, content, created_by="crawler", metadata="{}"):
    cur = conn.execute(
        "INSERT INTO knowledge_items(org_id,type,title,content,is_active,created_by,metadata) "
        "VALUES(1,?,?,?,1,?,?)", (typ, title, content, created_by, metadata))
    return cur.lastrowid
