"""Tests for v2/core/judging/calculator.py."""
import pytest
from v2.core.database.schema import create_all
from v2.core.judging import db as jdb
from v2.core.judging.calculator import export_csv, get_event_progress, get_leaderboard


@pytest.fixture
def conn_factory():
    def _make():
        return create_all(":memory:")
    return _make


@pytest.fixture
def conn():
    c = create_all(":memory:")
    yield c
    c.close()


@pytest.fixture
def seeded(conn_factory):
    conn = conn_factory()
    eid = jdb.create_event(conn, "3MRP", criteria=["Q1", "Q2"], top_n=3, min_coverage=2)
    jdb.set_event_status(conn, eid, "open")
    jdb.load_presenters_csv(
        conn, eid, "100,Jane Smith,CS\n101,Ali Hassan,EE\n102,Maria Garcia,Bio"
    )
    j1 = jdb.add_judge(conn, eid, "Judge A", "PA")
    j2 = jdb.add_judge(conn, eid, "Judge B", "PB")
    jdb.authenticate_judge(conn, eid, "PA", "tg1")
    jdb.authenticate_judge(conn, eid, "PB", "tg2")
    # 100: both judges give 4,4 → avg 4.0
    jdb.submit_score(conn, eid, j1, 100, ["Q1", "Q2"], [4, 4])
    jdb.submit_score(conn, eid, j2, 100, ["Q1", "Q2"], [4, 4])
    # 101: both give 5,3 and 3,5 → avg 4.0 (tie with 100)
    jdb.submit_score(conn, eid, j1, 101, ["Q1", "Q2"], [5, 3])
    jdb.submit_score(conn, eid, j2, 101, ["Q1", "Q2"], [3, 5])
    # 102: only judge A gives 3,3 → avg 3.0, judge_count=1 (below min_coverage=2)
    jdb.submit_score(conn, eid, j1, 102, ["Q1", "Q2"], [3, 3])
    conn.commit()
    yield conn, eid
    conn.close()


def test_leaderboard_sorted_desc(seeded):
    conn, eid = seeded
    lb = get_leaderboard(conn, eid)
    scores = [r["avg_score"] for r in lb if r["avg_score"] is not None]
    assert scores == sorted(scores, reverse=True)


def test_ties_share_rank(seeded):
    conn, eid = seeded
    lb = get_leaderboard(conn, eid)
    rank1_entries = [r for r in lb if r["avg_score"] == 4.0]
    assert len(rank1_entries) == 2
    assert rank1_entries[0]["rank"] == rank1_entries[1]["rank"] == 1


def test_lower_scorer_has_higher_rank_number(seeded):
    conn, eid = seeded
    lb = get_leaderboard(conn, eid)
    rank3 = next(r for r in lb if r["avg_score"] == 3.0)
    assert rank3["rank"] == 3


def test_unscored_presenter_has_none_rank(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.load_presenters_csv(conn, eid, "100,Jane,CS")
    conn.commit()
    lb = get_leaderboard(conn, eid)
    assert lb[0]["rank"] is None
    assert lb[0]["avg_score"] is None


def test_low_coverage_flag(seeded):
    conn, eid = seeded
    lb = get_leaderboard(conn, eid, min_coverage=2)
    # presenter 102 has only 1 judge → flagged
    row_102 = next(r for r in lb if r["number"] == 102)
    assert row_102["low_coverage"] is True
    # presenters 100 and 101 have 2 judges → not flagged
    row_100 = next(r for r in lb if r["number"] == 100)
    assert row_100["low_coverage"] is False


def test_no_low_coverage_when_min_coverage_none(seeded):
    conn, eid = seeded
    lb = get_leaderboard(conn, eid, min_coverage=None)
    assert all(not r["low_coverage"] for r in lb)


def test_progress_all_scored(conn):
    eid = jdb.create_event(conn, "3MRP", criteria=["Q1", "Q2"], top_n=3)
    jdb.set_event_status(conn, eid, "open")
    jdb.load_presenters_csv(conn, eid, "100,Jane,CS\n101,Ali,EE\n102,Maria,Bio")
    j1 = jdb.add_judge(conn, eid, "Judge A", "PA")
    j2 = jdb.add_judge(conn, eid, "Judge B", "PB")
    jdb.authenticate_judge(conn, eid, "PA", "tg1")
    jdb.authenticate_judge(conn, eid, "PB", "tg2")
    jdb.submit_score(conn, eid, j1, 100, ["Q1", "Q2"], [4, 4])
    jdb.submit_score(conn, eid, j2, 100, ["Q1", "Q2"], [4, 4])
    jdb.submit_score(conn, eid, j1, 101, ["Q1", "Q2"], [5, 3])
    jdb.submit_score(conn, eid, j2, 101, ["Q1", "Q2"], [3, 5])
    jdb.submit_score(conn, eid, j1, 102, ["Q1", "Q2"], [3, 3])
    jdb.submit_score(conn, eid, j2, 102, ["Q1", "Q2"], [3, 3])
    conn.commit()
    p = get_event_progress(conn, eid)
    assert p["total_judges"] == 2
    assert p["authenticated_judges"] == 2
    assert p["total_presenters"] == 3
    assert p["scores_submitted"] == 6
    assert p["max_possible"] == 6
    assert p["coverage_pct"] == 100.0


def test_progress_present_presenters(conn):
    eid = jdb.create_event(conn, "Test")
    jdb.load_presenters_csv(conn, eid, "100,Jane,CS\n101,Ali,EE")
    jdb.mark_presenter_present(conn, eid, 100)
    conn.commit()
    p = get_event_progress(conn, eid)
    assert p["present_presenters"] == 1
    assert p["total_presenters"] == 2


def test_progress_empty_event(conn):
    eid = jdb.create_event(conn, "Test")
    conn.commit()
    p = get_event_progress(conn, eid)
    assert p["coverage_pct"] == 0.0
    assert p["max_possible"] == 0


def test_export_csv_header(seeded):
    conn, eid = seeded
    csv_text = export_csv(conn, eid)
    header = csv_text.splitlines()[0]
    assert "rank" in header
    assert "number" in header
    assert "final_score" in header
    assert "low_coverage" in header


def test_export_csv_contains_names(seeded):
    conn, eid = seeded
    csv_text = export_csv(conn, eid)
    assert "Jane Smith" in csv_text
    assert "Ali Hassan" in csv_text
    assert "Maria Garcia" in csv_text


def test_export_csv_row_count(seeded):
    conn, eid = seeded
    csv_text = export_csv(conn, eid)
    lines = csv_text.strip().splitlines()
    assert len(lines) == 4  # 1 header + 3 presenters


def test_export_csv_low_coverage_flag(seeded):
    conn, eid = seeded
    csv_text = export_csv(conn, eid)
    lines = csv_text.strip().splitlines()
    # Find line for presenter 102 (only 1 judge → low coverage)
    line_102 = next(l for l in lines[1:] if l.startswith("3,102,") or ",102," in l)
    assert line_102.endswith("yes")
