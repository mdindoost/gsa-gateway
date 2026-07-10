from facultyfolio import rank, db


def test_ywcc_rollup_totals():
    ywcc = db.org_node_by_slug("ywcc")
    org_ids = [d["node_id"] for d in db.dept_orgs_of_college(ywcc)] + [ywcc]
    r = rank.funding_rollup(org_ids)
    assert r["nsf"] == 37401075
    assert r["nih"] == 6076611
    assert r["n_funded"] == 36


def test_data_science_has_no_nih():
    ds = db.org_node_by_slug("data-science")
    r = rank.funding_rollup([ds])
    assert r["nih"] == 0
    assert r["nsf"] > 0


def test_empty_subtree_returns_none():
    # a real org with no funded faculty -> None (use an org id unlikely to have funding)
    assert rank.funding_rollup([]) is None
