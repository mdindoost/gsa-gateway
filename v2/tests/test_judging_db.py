"""Tests for v2/core/judging/db.py — all run against an in-memory SQLite DB."""
import pytest
from v2.core.database.schema import create_all
from v2.core.judging import db as jdb


@pytest.fixture
def conn():
    c = create_all(":memory:")
    yield c
    c.close()


def test_create_event_defaults(conn):
    eid = jdb.create_event(conn, "3MRP 2026")
    conn.commit()
    ev = jdb.get_event(conn, eid)
    assert ev["name"] == "3MRP 2026"
    assert ev["status"] == "setup"
    assert ev["criteria"] == jdb.DEFAULT_CRITERIA
    assert ev["top_n"] == 3


def test_create_event_custom_criteria(conn):
    eid = jdb.create_event(conn, "Research Day", criteria=["Q1", "Q2"], top_n=1)
    conn.commit()
    ev = jdb.get_event(conn, eid)
    assert ev["criteria"] == ["Q1", "Q2"]
    assert ev["top_n"] == 1


def test_get_open_event_none_while_setup(conn):
    jdb.create_event(conn, "Test")
    conn.commit()
    assert jdb.get_open_event(conn) is None


def test_open_event_visible(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.set_event_status(conn, eid, "open")
    conn.commit()
    ev = jdb.get_open_event(conn)
    assert ev is not None
    assert ev["id"] == eid
    assert ev["status"] == "open"


def test_list_events(conn):
    jdb.create_event(conn, "A")
    jdb.create_event(conn, "B")
    conn.commit()
    events = jdb.list_events(conn)
    assert len(events) == 2


def test_update_event(conn):
    eid = jdb.create_event(conn, "Old")
    conn.commit()
    jdb.update_event(conn, eid, name="New", top_n=5)
    conn.commit()
    ev = jdb.get_event(conn, eid)
    assert ev["name"] == "New"
    assert ev["top_n"] == 5


def test_add_and_authenticate_judge(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.set_event_status(conn, eid, "open")
    jdb.add_judge(conn, eid, "Amira", "J-001")
    conn.commit()
    judge = jdb.authenticate_judge(conn, eid, "J-001", "tg_user_1")
    conn.commit()
    assert judge is not None
    assert judge["name"] == "Amira"


def test_wrong_pin_returns_none(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.add_judge(conn, eid, "Amira", "J-001")
    conn.commit()
    assert jdb.authenticate_judge(conn, eid, "WRONG", "tg1") is None


def test_pin_cannot_be_claimed_by_two_telegram_users(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.add_judge(conn, eid, "Amira", "J-001")
    conn.commit()
    jdb.authenticate_judge(conn, eid, "J-001", "tg_user_1")
    conn.commit()
    result = jdb.authenticate_judge(conn, eid, "J-001", "tg_user_2")
    assert result is None


def test_same_telegram_user_can_re_authenticate(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.add_judge(conn, eid, "Amira", "J-001")
    conn.commit()
    jdb.authenticate_judge(conn, eid, "J-001", "tg1")
    conn.commit()
    result = jdb.authenticate_judge(conn, eid, "J-001", "tg1")
    assert result is not None
    assert result["name"] == "Amira"


def test_get_judge_by_telegram_hash(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.add_judge(conn, eid, "Amira", "J-001")
    conn.commit()
    jdb.authenticate_judge(conn, eid, "J-001", "tg123")
    conn.commit()
    result = jdb.get_judge_by_telegram_hash(conn, eid, "tg123")
    assert result is not None
    assert result["name"] == "Amira"


def test_get_judge_by_telegram_hash_not_found(conn):
    eid = jdb.create_event(conn, "Test")
    conn.commit()
    assert jdb.get_judge_by_telegram_hash(conn, eid, "nobody") is None


def test_list_judges(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.add_judge(conn, eid, "A", "P1")
    jdb.add_judge(conn, eid, "B", "P2")
    conn.commit()
    judges = jdb.list_judges(conn, eid)
    assert len(judges) == 2
    assert all(not j["authenticated"] for j in judges)


def test_load_presenters_csv_with_header(conn):
    eid = jdb.create_event(conn, "Test")
    conn.commit()
    csv = "number,name,department\n100,Jane Smith,CS\n101,Ali Hassan,EE"
    count = jdb.load_presenters_csv(conn, eid, csv)
    conn.commit()
    assert count == 2
    p = jdb.get_presenter(conn, eid, 100)
    assert p["name"] == "Jane Smith"
    assert p["department"] == "CS"


def test_load_presenters_csv_no_header(conn):
    eid = jdb.create_event(conn, "Test")
    conn.commit()
    count = jdb.load_presenters_csv(conn, eid, "200,Bob Lee,Math\n201,Sara Kim,Bio")
    conn.commit()
    assert count == 2


def test_load_presenters_csv_no_department(conn):
    eid = jdb.create_event(conn, "Test")
    conn.commit()
    count = jdb.load_presenters_csv(conn, eid, "300,Pat Jones")
    conn.commit()
    assert count == 1
    p = jdb.get_presenter(conn, eid, 300)
    assert p["department"] == ""


def test_has_scored_false_initially(conn):
    eid = jdb.create_event(conn, "Test")
    jid = jdb.add_judge(conn, eid, "Judge", "P1")
    conn.commit()
    assert jdb.has_scored(conn, eid, jid, 100) is False


def test_submit_and_has_scored(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.load_presenters_csv(conn, eid, "100,Jane,CS")
    jid = jdb.add_judge(conn, eid, "Judge", "P1")
    conn.commit()
    jdb.submit_score(conn, eid, jid, 100, ["Q1", "Q2"], [4, 5])
    conn.commit()
    assert jdb.has_scored(conn, eid, jid, 100) is True


def test_delete_score_allows_rescore(conn):
    eid = jdb.create_event(conn, "Test")
    jid = jdb.add_judge(conn, eid, "Judge", "P1")
    conn.commit()
    jdb.submit_score(conn, eid, jid, 100, ["Q1"], [3])
    conn.commit()
    deleted = jdb.delete_score(conn, eid, jid, 100)
    conn.commit()
    assert deleted is True
    assert jdb.has_scored(conn, eid, jid, 100) is False


def test_delete_score_nonexistent_returns_false(conn):
    eid = jdb.create_event(conn, "Test")
    jid = jdb.add_judge(conn, eid, "Judge", "P1")
    conn.commit()
    assert jdb.delete_score(conn, eid, jid, 999) is False
