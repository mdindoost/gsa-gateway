from v2.core.retrieval import entity
from v2.core.ingestion.entity_mentions import build_mentions, write_mentions
from v2.tests._em_fixtures import new_db, add_person, add_item


def _db():
    c = new_db()
    add_person(c, "k/oria", "Oria, Vincent")
    add_person(c, "k/deek", "Deek, Fadi")
    # awards (id-linked) — one real, one bare-year noise
    add_item(c, "award", "2015 ACM SIGMOD Test-of-Time Award", "x", created_by="crawler",
             metadata='{"entity_id":"k/deek"}')
    add_item(c, "award", "2011", "bare-year-noise", created_by="crawler",
             metadata='{"entity_id":"k/deek"}')
    # curated bio (title-tagged to Oria) + a news item mentioning Oria
    add_item(c, "faq", "Who is Prof. Vincent Oria?",
             "Vincent Oria is a Professor and Chair. He chaired MMI 2025 and 2026.",
             created_by="migration")
    c.execute("UPDATE knowledge_items SET source_url='http://njit/bio' WHERE type='faq'")
    add_item(c, "news", "Deek memoir", "Fadi Deek published a memoir.",
             created_by="college_crawl", metadata='{"natural_key":"nk-memoir"}')
    c.execute("UPDATE knowledge_items SET source_url='http://njit/news' WHERE type='news'")
    # a service row for Oria (id-linked) for involvement
    add_item(c, "service", "Program Committee, ACM SIGMOD", "committee work", created_by="crawler",
             metadata='{"entity_id":"k/oria"}')
    c.commit()
    write_mentions(c, build_mentions(c))
    return c


def test_awards_of_person_drops_bare_year():
    r = entity.awards_of_person(_db(), "k/deek")
    assert r["name"] == "Fadi Deek"
    assert "2015 ACM SIGMOD Test-of-Time Award" in r["awards"] and "2011" not in r["awards"]


def test_awards_empty():
    r = entity.awards_of_person(_db(), "k/oria")
    assert r["awards"] == []


def test_news_of_person():
    r = entity.news_of_person(_db(), "k/deek")
    assert any(it["title"] == "Deek memoir" and it["url"] == "http://njit/news" for it in r["items"])


def test_bio_prefers_title_faq():
    r = entity.bio_of_person(_db(), "k/oria")
    assert r["text"].startswith("Vincent Oria is a Professor") and r["url"] == "http://njit/bio"


def test_bio_empty_for_person_without_bio():
    r = entity.bio_of_person(_db(), "k/deek")
    assert not r["text"]


def test_involvement_rolls_up_and_dedups():
    r = entity.involvement_of_person(_db(), "k/oria")
    titles = [it["title"] for it in r["items"]]
    assert any("SIGMOD" in t for t in titles)          # service row
    assert any("Oria" in t for t in titles)            # tagged bio faq
    # no duplicate stable_keys
    keys = [it.get("title") for it in r["items"]]
    assert len(keys) == len(set(keys))
