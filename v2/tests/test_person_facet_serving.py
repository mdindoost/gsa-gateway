from v2.core.retrieval import structured_answer as sa
from v2.core.retrieval.router import Route
from v2.core.ingestion.entity_mentions import build_mentions, write_mentions
from v2.tests._em_fixtures import new_db, add_person, add_item


def _db():
    c = new_db()
    add_person(c, "k/deek", "Deek, Fadi")
    add_item(c, "award", "2022 Lifetime Achievement Award, NJIT", "x", created_by="crawler",
             metadata='{"entity_id":"k/deek"}')
    add_item(c, "news", "Deek memoir", "Fadi Deek published a memoir.", created_by="college_crawl",
             metadata='{"natural_key":"nk-m"}')
    c.execute("UPDATE knowledge_items SET source_url='http://n' WHERE type='news'")
    c.commit()
    write_mentions(c, build_mentions(c))
    return c


def _run(skill, eid="k/deek"):
    c = _db()
    result = sa.run(c, Route(skill=skill, args={"entity_id": eid, "name": "Fadi Deek"}))
    return result, sa.format_answer(result)


def test_all_four_are_deterministic():
    for skill in ("awards_of_person", "news_of_person", "bio_of_person", "involvement_of_person"):
        result, _ = _run(skill)
        assert sa.is_deterministic(result), skill


def test_awards_answer():
    _, txt = _run("awards_of_person")
    assert "Lifetime Achievement" in txt and "Fadi Deek" in txt


def test_awards_empty_honest():
    c = new_db(); add_person(c, "k/x", "Nobody, X"); c.commit()
    result = sa.run(c, Route(skill="awards_of_person", args={"entity_id": "k/x", "name": "X Nobody"}))
    assert "don't have awards" in sa.format_answer(result).lower()


def test_news_answer_has_link():
    _, txt = _run("news_of_person")
    assert "Deek memoir" in txt and "http://n" in txt


def test_bio_empty_falls_to_rag():
    _, txt = _run("bio_of_person")           # Deek has no curated bio here
    assert txt == ""                          # empty -> RAG


def test_involvement_answer():
    _, txt = _run("involvement_of_person")
    assert "Fadi Deek" in txt and "memoir" in txt.lower()


def test_person_names_tagged():
    result, _ = _run("awards_of_person")
    assert sa.person_names_of(result) == ["Fadi Deek"]
