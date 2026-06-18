"""Tests for v2/core/judging/db.py — all run against an in-memory SQLite DB."""
import os
import pytest

os.environ.setdefault("GSA_JUDGING_SCRYPT_N", "64")  # fast scrypt for tests

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
    assert ev["score_min"] == 1
    assert ev["score_max"] == 5
    assert ev["min_coverage"] == 3


def test_create_event_custom_params(conn):
    eid = jdb.create_event(conn, "Research Day", criteria=["Q1", "Q2"],
                            top_n=1, score_min=0, score_max=10, min_coverage=5)
    conn.commit()
    ev = jdb.get_event(conn, eid)
    assert ev["criteria"] == ["Q1", "Q2"]
    assert ev["top_n"] == 1
    assert ev["score_min"] == 0
    assert ev["score_max"] == 10
    assert ev["min_coverage"] == 5


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


def test_get_any_event_setup(conn):
    eid = jdb.create_event(conn, "Not Yet Open")
    conn.commit()
    ev = jdb.get_any_event(conn)
    assert ev is not None
    assert ev["id"] == eid
    assert ev["status"] == "setup"


def test_get_any_event_returns_latest(conn):
    jdb.create_event(conn, "Old")
    eid2 = jdb.create_event(conn, "New")
    conn.commit()
    ev = jdb.get_any_event(conn)
    assert ev["id"] == eid2


def test_get_any_event_none_if_no_events(conn):
    assert jdb.get_any_event(conn) is None


def test_list_events(conn):
    jdb.create_event(conn, "A")
    jdb.create_event(conn, "B")
    conn.commit()
    events = jdb.list_events(conn)
    assert len(events) == 2


def test_update_event_all_fields(conn):
    eid = jdb.create_event(conn, "Old")
    conn.commit()
    jdb.update_event(conn, eid, name="New", top_n=5,
                     score_min=0, score_max=10, min_coverage=6)
    conn.commit()
    ev = jdb.get_event(conn, eid)
    assert ev["name"] == "New"
    assert ev["top_n"] == 5
    assert ev["score_min"] == 0
    assert ev["score_max"] == 10
    assert ev["min_coverage"] == 6


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
    assert all(j["has_pin"] for j in judges)
    assert "pin" not in judges[0]  # C1: PIN hash never returned


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
    assert p["is_present"] is False


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


def test_register_presenter(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.load_presenters_csv(conn, eid, "100,Jane,CS")
    conn.commit()
    ok = jdb.register_presenter(conn, eid, 100, "tg_presenter_1")
    conn.commit()
    assert ok is True
    p = jdb.get_presenter(conn, eid, 100)
    assert p["is_present"] is True
    assert p["has_telegram"] is True


def test_register_presenter_wrong_number(conn):
    eid = jdb.create_event(conn, "Test")
    conn.commit()
    ok = jdb.register_presenter(conn, eid, 999, "tg1")
    assert ok is False


def test_register_presenter_already_taken_by_other(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.load_presenters_csv(conn, eid, "100,Jane,CS")
    conn.commit()
    jdb.register_presenter(conn, eid, 100, "tg_user_a")
    conn.commit()
    ok = jdb.register_presenter(conn, eid, 100, "tg_user_b")
    assert ok is False


def test_mark_presenter_present(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.load_presenters_csv(conn, eid, "100,Jane,CS")
    conn.commit()
    jdb.mark_presenter_present(conn, eid, 100)
    conn.commit()
    p = jdb.get_presenter(conn, eid, 100)
    assert p["is_present"] is True
    assert p["has_telegram"] is False


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


def test_get_score_returns_scores(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.load_presenters_csv(conn, eid, "100,Jane,CS")
    jid = jdb.add_judge(conn, eid, "Judge", "P1")
    conn.commit()
    jdb.submit_score(conn, eid, jid, 100, ["Q1", "Q2"], [4, 5])
    conn.commit()
    result = jdb.get_score(conn, eid, jid, 100)
    assert result is not None
    assert result["scores"]["Q1"] == 4
    assert result["scores"]["Q2"] == 5


def test_get_score_none_before_submit(conn):
    eid = jdb.create_event(conn, "Test")
    jid = jdb.add_judge(conn, eid, "Judge", "P1")
    conn.commit()
    assert jdb.get_score(conn, eid, jid, 100) is None


def test_get_all_scores_by_judge(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.load_presenters_csv(conn, eid, "100,Jane,CS\n101,Ali,EE")
    jid = jdb.add_judge(conn, eid, "Judge", "P1")
    conn.commit()
    jdb.submit_score(conn, eid, jid, 100, ["Q1"], [4])
    jdb.submit_score(conn, eid, jid, 101, ["Q1"], [3])
    conn.commit()
    scored = jdb.get_all_scores_by_judge(conn, eid, jid)
    assert len(scored) == 2
    assert scored[0]["number"] == 100
    assert scored[1]["number"] == 101


def test_get_presenter_scores_detail(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.load_presenters_csv(conn, eid, "100,Jane,CS")
    jid1 = jdb.add_judge(conn, eid, "Judge A", "P1")
    jid2 = jdb.add_judge(conn, eid, "Judge B", "P2")
    conn.commit()
    jdb.submit_score(conn, eid, jid1, 100, ["Q1"], [4])
    jdb.submit_score(conn, eid, jid2, 100, ["Q1"], [5])
    conn.commit()
    detail = jdb.get_presenter_scores_detail(conn, eid, 100)
    assert len(detail) == 2
    names = {d["judge_name"] for d in detail}
    assert names == {"Judge A", "Judge B"}


def test_delete_score_allows_rescore(conn):
    eid = jdb.create_event(conn, "Test")
    jid = jdb.add_judge(conn, eid, "Judge", "P1")
    jdb.load_presenters_csv(conn, eid, "100,Jane Smith,CS")  # M-new-2: presenter must exist
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


# ── admin upsert + score audit (Task #4) ────────────────────────────────────────

def _score_setup(conn):
    eid = jdb.create_event(conn, "Test", criteria=["Q1", "Q2"], score_min=1, score_max=5)
    jid = jdb.add_judge(conn, eid, "Judge", "PIN123")
    jdb.load_presenters_csv(conn, eid, "100,Jane Smith,CS")
    conn.commit()
    return eid, jid


def test_submit_score_returns_json_and_final(conn):
    eid, jid = _score_setup(conn)
    scores_json, final = jdb.submit_score(conn, eid, jid, 100, ["Q1", "Q2"], [4, 5])
    conn.commit()
    assert final == 4.5                       # mean, not sum
    assert '"Q1": 4' in scores_json and '"Q2": 5' in scores_json


def test_upsert_score_insert_then_edit(conn):
    eid, jid = _score_setup(conn)
    existed, _, final = jdb.upsert_score(conn, eid, jid, 100, ["Q1", "Q2"], [2, 2])
    conn.commit()
    assert existed is False and final == 2.0  # first time → enter
    existed, _, final = jdb.upsert_score(conn, eid, jid, 100, ["Q1", "Q2"], [5, 5])
    conn.commit()
    assert existed is True and final == 5.0   # second time → edit (overwrite)
    assert jdb.get_score(conn, eid, jid, 100)["scores"] == {"Q1": 5, "Q2": 5}


def test_upsert_score_rejects_out_of_range(conn):
    eid, jid = _score_setup(conn)
    with pytest.raises(ValueError):
        jdb.upsert_score(conn, eid, jid, 100, ["Q1", "Q2"], [9, 1])  # 9 > max 5


def test_submit_score_duplicate_still_raises(conn):
    # The judge path's INSERT-only behavior (C2 guard) must survive the refactor.
    import sqlite3
    eid, jid = _score_setup(conn)
    jdb.submit_score(conn, eid, jid, 100, ["Q1", "Q2"], [4, 5])
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        jdb.submit_score(conn, eid, jid, 100, ["Q1", "Q2"], [3, 3])


def test_log_and_get_score_audit(conn):
    eid, jid = _score_setup(conn)
    jdb.log_score_audit(conn, eid, jid, 100, action="submit", actor="judge",
                        actor_label="Judge", scores_json='{"Q1": 4}', final_score=4.0)
    jdb.log_score_audit(conn, eid, jid, 100, action="admin_edit", actor="admin",
                        actor_label="admin", scores_json='{"Q1": 5}', final_score=5.0)
    conn.commit()
    trail = jdb.get_score_audit(conn, eid)
    assert len(trail) == 2
    assert trail[0]["action"] == "admin_edit"   # newest first
    assert trail[0]["actor"] == "admin"
    assert trail[1]["action"] == "submit"
    assert trail[0]["judge_name"] == "Judge"


def test_audit_atomic_with_score(conn):
    # A failed score write in the same transaction must leave NO audit row.
    import sqlite3
    eid, jid = _score_setup(conn)
    jdb.submit_score(conn, eid, jid, 100, ["Q1", "Q2"], [4, 5])
    conn.commit()
    try:
        jdb.submit_score(conn, eid, jid, 100, ["Q1", "Q2"], [3, 3])   # duplicate → raises
        jdb.log_score_audit(conn, eid, jid, 100, action="submit", actor="judge")
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
    assert jdb.get_score_audit(conn, eid) == []   # rolled back, no orphan audit row


def test_delete_event_cascades_everything(conn):
    eid, jid = _score_setup(conn)
    jdb.submit_score(conn, eid, jid, 100, ["Q1", "Q2"], [4, 5])
    jdb.log_score_audit(conn, eid, jid, 100, action="submit", actor="judge")
    jdb.cast_vote(conn, eid, "voter_x", 100)
    conn.commit()
    # sanity: children exist
    assert conn.execute("SELECT COUNT(*) FROM judging_judges WHERE event_id=?", (eid,)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM judging_scores WHERE event_id=?", (eid,)).fetchone()[0] == 1
    deleted = jdb.delete_event(conn, eid)
    conn.commit()
    assert deleted is True
    # event + ALL children gone (ON DELETE CASCADE)
    for tbl in ("judging_events", "judging_judges", "judging_presenters",
                "judging_scores", "judging_audience_votes", "judging_score_audit"):
        n = conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE "
                         f"{'id' if tbl=='judging_events' else 'event_id'}=?", (eid,)).fetchone()[0]
        assert n == 0, f"{tbl} still has {n} rows after delete_event"


def test_delete_event_nonexistent_returns_false(conn):
    assert jdb.delete_event(conn, 99999) is False


def test_audit_delete_has_null_scores(conn):
    eid, jid = _score_setup(conn)
    jdb.submit_score(conn, eid, jid, 100, ["Q1", "Q2"], [4, 5])
    jdb.log_score_audit(conn, eid, jid, 100, action="admin_delete", actor="admin")
    conn.commit()
    trail = jdb.get_score_audit(conn, eid)
    assert trail[0]["action"] == "admin_delete"
    assert trail[0]["scores"] is None and trail[0]["final_score"] is None


# ── Audience votes ─────────────────────────────────────────────────────────────

def test_cast_vote_and_get_vote(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.load_presenters_csv(conn, eid, "100,Jane,CS")
    conn.commit()
    jdb.cast_vote(conn, eid, "tg_voter_1", 100)
    conn.commit()
    v = jdb.get_vote(conn, eid, "tg_voter_1")
    assert v is not None
    assert v["presenter_number"] == 100
    assert v["name"] == "Jane"


def test_cast_vote_replaces_previous(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.load_presenters_csv(conn, eid, "100,Jane,CS\n101,Ali,EE")
    conn.commit()
    jdb.cast_vote(conn, eid, "tg_voter_1", 100)
    conn.commit()
    jdb.cast_vote(conn, eid, "tg_voter_1", 101)
    conn.commit()
    v = jdb.get_vote(conn, eid, "tg_voter_1")
    assert v["presenter_number"] == 101


def test_get_vote_none_before_voting(conn):
    eid = jdb.create_event(conn, "Test")
    conn.commit()
    assert jdb.get_vote(conn, eid, "nobody") is None


def test_audience_results_sorted(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.load_presenters_csv(conn, eid, "100,Jane,CS\n101,Ali,EE\n102,Maria,Bio")
    conn.commit()
    jdb.cast_vote(conn, eid, "voter1", 101)
    jdb.cast_vote(conn, eid, "voter2", 101)
    jdb.cast_vote(conn, eid, "voter3", 100)
    conn.commit()
    results = jdb.get_audience_results(conn, eid)
    assert results[0]["number"] == 101
    assert results[0]["vote_count"] == 2
    assert results[0]["rank"] == 1
    assert results[1]["number"] == 100
    assert results[1]["vote_count"] == 1
    assert results[1]["rank"] == 2


def test_audience_results_ties_share_rank(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.load_presenters_csv(conn, eid, "100,Jane,CS\n101,Ali,EE")
    conn.commit()
    jdb.cast_vote(conn, eid, "voter1", 100)
    jdb.cast_vote(conn, eid, "voter2", 101)
    conn.commit()
    results = jdb.get_audience_results(conn, eid)
    assert results[0]["rank"] == results[1]["rank"] == 1


def test_audience_results_zero_votes_no_rank(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.load_presenters_csv(conn, eid, "100,Jane,CS")
    conn.commit()
    results = jdb.get_audience_results(conn, eid)
    assert results[0]["rank"] is None
    assert results[0]["vote_count"] == 0


def test_set_audience_voting(conn):
    eid = jdb.create_event(conn, "Test")
    conn.commit()
    ev = jdb.get_event(conn, eid)
    assert ev["audience_voting"] == "closed"
    jdb.set_audience_voting(conn, eid, "open")
    conn.commit()
    ev = jdb.get_event(conn, eid)
    assert ev["audience_voting"] == "open"


def test_audience_top_n_default(conn):
    eid = jdb.create_event(conn, "Test")
    conn.commit()
    ev = jdb.get_event(conn, eid)
    assert ev["audience_top_n"] == 1


def test_audience_top_n_custom(conn):
    eid = jdb.create_event(conn, "Test", audience_top_n=3)
    conn.commit()
    ev = jdb.get_event(conn, eid)
    assert ev["audience_top_n"] == 3
