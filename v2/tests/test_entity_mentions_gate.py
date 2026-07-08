from v2.core.ingestion.entity_mentions import resolve_item, PersonName

ORIA = PersonName(15, "people.njit.edu/profile/oria", "Oria", "Vincent")
SATOH = PersonName(20, "x/satoh", "Satoh", "Shinichi")


def many(n):
    return [PersonName(100 + i, f"x/p{i}", f"Last{i}", f"First{i}") for i in range(n)]


def test_title_fastpath_accepts_bio():
    out = resolve_item("Who is Prof. Vincent Oria?", "Vincent Oria is a Professor ...", [ORIA])
    assert out and out[0][0].node_key == ORIA.node_key and out[0][1] == "title"


def test_memorial_substring_rejected():
    # 'Oria' inside 'Memorial'; first name absent -> not both-names -> no match
    out = resolve_item("Award", "2010 Franklin V. Taylor Memorial Award", [ORIA])
    assert out == []


def test_both_names_body_accepts_news():
    out = resolve_item("MMI 2026",
                       "The organizing committee was Vincent Oria (NJIT) and Shinichi Satoh.",
                       [ORIA, SATOH])
    keys = {p.node_key for p, _, _ in out}
    assert ORIA.node_key in keys and SATOH.node_key in keys


def test_roster_page_rejected():
    # target appears once + many other known people -> roster
    people = [ORIA] + many(6)
    body = "Professor Oria, Vincent Professor " + " ".join(f"First{i} Last{i}" for i in range(6))
    out = resolve_item("Ph.D. Computer Science", body, people)
    assert all(p.node_key != ORIA.node_key for p, _, _ in out)


def test_multiperson_news_accepted_not_roster():
    # a genuine news item names a few collaborators but is ABOUT the subject (named twice) -> accept
    body = ("From Byblos to Newark: Fadi Deek’s memoir. Fadi Deek reflects with colleagues "
            "First0 Last0 and First1 Last1.")
    deek = PersonName(9, "x/deek", "Deek", "Fadi")
    out = resolve_item("News", body, [deek] + many(2))
    assert any(p.node_key == "x/deek" for p, _, _ in out)


def test_namesake_abstains():
    # two active nodes share the full name -> abstain (skip), never a wrong tag
    a = PersonName(1, "x/a", "Smith", "John")
    b = PersonName(2, "x/b", "Smith", "John")
    out = resolve_item("News", "John Smith presented today.", [a, b])
    assert out == []
