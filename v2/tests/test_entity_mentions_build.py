from v2.core.ingestion.entity_mentions import build_mentions, write_mentions
from v2.tests._em_fixtures import new_db, add_person, add_item


def _fixture():
    conn = new_db()
    add_person(conn, "k/oria", "Oria, Vincent")
    add_person(conn, "k/satoh", "Satoh, Shinichi")
    # curated bio (no natural_key) -> stable_key must fall back to id:
    add_item(conn, "faq", "Who is Prof. Vincent Oria?",
             "Vincent Oria is a Professor and Chair.", created_by="migration")
    # news naming both, with a natural_key
    add_item(conn, "news", "MMI", "Committee: Vincent Oria and Shinichi Satoh.",
             created_by="college_crawl", metadata='{"natural_key":"nk-mmi"}')
    conn.commit()
    return conn


def test_build_tags_bio_and_news():
    conn = _fixture()
    rows = build_mentions(conn)
    by_person = {(r["node_key"], r["title"][:5]) for r in rows}
    assert ("k/oria", "Who i") in by_person          # bio -> Oria (title fast-path)
    assert ("k/oria", "MMI") in by_person and ("k/satoh", "MMI") in by_person
    bio = next(r for r in rows if r["title"].startswith("Who"))
    assert bio["stable_key"].startswith("id:")       # no natural_key -> id: prefix
    news = next(r for r in rows if r["title"] == "MMI")
    assert news["stable_key"] == "nk-mmi"


def test_write_then_count():
    conn = _fixture()
    n = write_mentions(conn, build_mentions(conn))
    assert n >= 3
    got = conn.execute("SELECT count(*) FROM entity_mentions WHERE node_key='k/oria'").fetchone()[0]
    assert got == 2                                   # bio + news


def test_write_is_full_rebuild_in_scope():
    conn = _fixture()
    write_mentions(conn, build_mentions(conn))
    # a foreign-scope row must survive; the tagger's own rows get replaced
    conn.execute("INSERT INTO entity_mentions(stable_key,node_key,item_id,node_id,match_basis,created_by) "
                 "VALUES('manual','k/x',1,1,'title','human')")
    conn.commit()
    write_mentions(conn, build_mentions(conn))
    assert conn.execute("SELECT count(*) FROM entity_mentions WHERE created_by='human'").fetchone()[0] == 1
