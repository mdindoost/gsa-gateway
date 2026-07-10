import datetime
from facultyfolio import rank, db

TODAY = datetime.date(2026, 7, 10)


def test_cs_rollup_counts():
    r = rank.funding_rollup([16], today=TODAY)          # CS org node id = 16
    assert r["nsf_awards"] == 59 and r["nsf_active"] == 14
    assert r["nih_projects"] == 5 and r["nih_active"] == 1
    assert r["funded"] == 23


def test_ywcc_rollup_counts():
    ywcc = db.org_node_by_slug("ywcc")
    ids = [d["node_id"] for d in db.dept_orgs_of_college(ywcc)] + [ywcc]
    r = rank.funding_rollup(ids, today=TODAY)
    assert r["nsf_awards"] == 92 and r["nsf_active"] == 25
    assert r["nih_projects"] == 5 and r["nih_active"] == 1
    assert r["funded"] == 36


def test_data_science_has_no_nih():
    r = rank.funding_rollup([db.org_node_by_slug("data-science")], today=TODAY)
    assert r["nsf_awards"] == 17 and r["nih_projects"] == 0
    assert r["funded"] == 7


def test_empty_subtree_returns_none():
    assert rank.funding_rollup([], today=TODAY) is None
