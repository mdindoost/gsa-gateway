from v2.core.retrieval.structured_answer import build_person_addendum, render_addendum
from v2.core.ingestion.entity_mentions import build_mentions, write_mentions
from v2.tests._em_fixtures import new_db, add_person, add_item


def _db_awards():
    c = new_db()
    add_person(c, "k/deek", "Deek, Fadi")
    add_item(c, "award", "2022 Lifetime Achievement Award, NJIT", "x", created_by="crawler",
             metadata='{"entity_id":"k/deek"}')
    add_item(c, "award", "2022", "noise-bare-year", created_by="crawler",
             metadata='{"entity_id":"k/deek"}')
    c.commit()
    return c


def test_awards_compact_drop_bare_year():
    p = build_person_addendum(_db_awards(), "k/deek", mentions_on=False)
    assert p and "Lifetime Achievement" in p["awards"]
    assert "noise-bare-year" not in p["awards"] and "; 2022" not in p["awards"]


def test_prose_join_and_isactive_filter():
    c = new_db()
    add_person(c, "k/oria", "Oria, Vincent")
    iid = add_item(c, "faq", "Who is Prof. Vincent Oria?",
                   "Vincent Oria is a Professor and Chair.", created_by="migration")
    write_mentions(c, build_mentions(c))
    p = build_person_addendum(c, "k/oria", mentions_on=True)
    assert p["prose"]["content"].startswith("Vincent Oria is a Professor")
    # deactivate the item -> the join drops it
    c.execute("UPDATE knowledge_items SET is_active=0 WHERE id=?", (iid,))
    c.commit()
    p2 = build_person_addendum(c, "k/oria", mentions_on=True)
    assert p2 is None or p2.get("prose") is None


def test_mentions_off_skips_prose():
    c = new_db()
    add_person(c, "k/oria", "Oria, Vincent")
    add_item(c, "faq", "Who is Prof. Vincent Oria?", "Vincent Oria is Chair.", created_by="migration")
    write_mentions(c, build_mentions(c))
    p = build_person_addendum(c, "k/oria", mentions_on=False)
    assert p is None                                 # no awards, prose gated off


def test_render_whole_if_fits_else_omit():
    payload = {"awards": None, "prose": {"title": "Bio", "content": "X" * 50, "url": "http://s"}}
    big = render_addendum(payload, used_len=0, platform_cap=4096)
    assert "X" * 50 in big                           # whole
    tight = render_addendum(payload, used_len=0, platform_cap=40)
    assert "X" * 50 not in tight                     # never partial
    assert "http://s" in tight                       # pointer instead


def test_render_none_when_empty():
    assert render_addendum({"awards": None, "prose": None}, used_len=0, platform_cap=2000) is None
    assert render_addendum(None, used_len=0, platform_cap=2000) is None
