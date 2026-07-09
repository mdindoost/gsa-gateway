from facultyfolio import db, rank

def _ywcc():
    return db.org_node_by_slug("ywcc")

def test_college_coverage_returns_two_ints():
    n, m = db.college_coverage(_ywcc())
    assert isinstance(n, int) and isinstance(m, int)
    assert 0 <= n <= m

def test_college_coverage_is_distinct_not_dept_sum():
    """Distinct people <= sum of dept coverages (dup-home faculty counted once)."""
    ywcc = _ywcc()
    depts = db.dept_orgs_of_college(ywcc)
    dept_sum_m = sum(rank.coverage(d["node_id"])[1] for d in depts)
    n, m = db.college_coverage(ywcc)
    assert m <= dept_sum_m           # distinct never exceeds the naive sum
    assert m >= max(rank.coverage(d["node_id"])[1] for d in depts)  # at least the biggest dept
