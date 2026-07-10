from facultyfolio import db, rank


def _ywcc():
    return db.org_node_by_slug("ywcc")


def test_rollup_totals_and_ladder_order():
    r = rank.college_rollup(_ywcc())
    assert r["total"] == 119 and r["with_scholar"] == 76
    assert r["groups"] == [
        ("Department Chair", 3), ("Distinguished Professor", 6), ("Professor", 13),
        ("Associate Professor", 16), ("Assistant Professor", 27),
        ("Senior University Lecturer", 31), ("University Lecturer", 21), ("Faculty", 2),
    ]


def test_rollup_total_equals_sum_of_group_counts():
    r = rank.college_rollup(_ywcc())
    assert sum(c for _, c in r["groups"]) == r["total"]


def test_rollup_no_duplicate_home_people():
    r = rank.college_rollup(_ywcc())          # de-dup assert would raise if a dup-home existed
    assert r["total"] == 119


def test_chairs_one_per_dept_labeled_by_department():
    chairs = rank.college_chairs(_ywcc())
    by_name = {c["name"]: c["dept_name"] for c in chairs}
    assert by_name["Vincent Oria"] == "Computer Science"
    assert by_name["James Geller"] == "Data Science"
    assert by_name["Michael Halper"] == "Informatics"
    assert all(c["rank_index"] == 0 for c in chairs)
    assert len(chairs) == 3
